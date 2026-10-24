"""Russian NK API v5.68 (2026-09-28). No speculative endpoints.

External writes are durable, never automatically retried after dispatch.
Secrets live in a separate 0600 file, excluded from DB exports/backups.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
import threading
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization.pkcs7 import load_der_pkcs7_certificates

from .excel import valid_gtin
from .service import Conflict, candidates, card_record
from .store import audit, dumps, now, uid

BASES = {"production": "https://апи.национальный-каталог.рф", "sandbox": "https://api.nk.sandbox.crptech.ru"}
STATUS = {"draft": "Черновик", "moderation": "На модерации", "errors": "Требует изменений", "notsigned": "Ожидает подписания", "published": "Опубликована", "archived": "Архив НК"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS nk_cards(card_id TEXT PRIMARY KEY REFERENCES cards(id),good_id INTEGER UNIQUE,raw TEXT NOT NULL,field_map TEXT NOT NULL,confirmed TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nk_schemas(cat_id TEXT PRIMARY KEY,body TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nk_scopes(card_id TEXT PRIMARY KEY REFERENCES cards(id),cat_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nk_snapshots(card_id TEXT NOT NULL,sha256 TEXT NOT NULL,raw TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(card_id,sha256));
CREATE TABLE IF NOT EXISTS nk_receipts(id INTEGER PRIMARY KEY,job_id TEXT NOT NULL REFERENCES nk_jobs(id),state TEXT NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nk_jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,card_id TEXT,revision INTEGER,actor TEXT NOT NULL,
 op_key TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,sha256 TEXT NOT NULL,state TEXT NOT NULL,result TEXT,error TEXT,
 external_id TEXT,lease TEXT,lease_until REAL,signature TEXT,next_at REAL NOT NULL DEFAULT 0,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS nk_payload_immutable BEFORE UPDATE OF payload,sha256,kind,card_id,revision,actor ON nk_jobs BEGIN SELECT RAISE(ABORT,'immutable NK command'); END;
CREATE TRIGGER IF NOT EXISTS nk_receipts_no_update BEFORE UPDATE ON nk_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END;
CREATE TRIGGER IF NOT EXISTS nk_receipts_no_delete BEFORE DELETE ON nk_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END;
"""


class RemoteError(ValueError):
    def __init__(self, message, code=0, retry=60):
        super().__init__(message)
        self.code, self.retry = code, retry


class Client:
    def __init__(self, config, transport=None):
        self.config, self.transport = config, transport
        self.retry_until=0

    def call(self, method, path, params=None, body=None):
        if time.time()<self.retry_until:
            raise RemoteError("НК ограничил частоту запросов; ожидается Retry-After",429,max(1,int(self.retry_until-time.time())))
        config = self.config()
        key = config.get("api_key")
        if not key:
            raise RemoteError("Введите API KEY Национального каталога в админке")
        try:
            with httpx.Client(timeout=45, follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, BASES[config.get("environment", "production")]+path,
                                          params={**(params or {}), "apikey": key, "format": "json"}, json=body,
                                          headers={"Accept": "application/json"})
        except (httpx.HTTPError, OSError):
            # Never include httpx exception text: its URL contains the API key.
            raise RemoteError("Соединение с НК прервано; исход внешнего действия может быть неизвестен") from None
        if response.status_code != 200:
            try:
                retry = min(3600, max(1, int(response.headers.get("Retry-After", "60"))))
            except ValueError:
                retry = 60
            if response.status_code==429:
                self.retry_until=time.time()+retry
            raise RemoteError(f"НК вернул HTTP {response.status_code}. Проверьте доступ и статус операции", response.status_code, retry)
        try:
            result = response.json()
        except ValueError:
            raise RemoteError("НК вернул ответ не в формате JSON") from None
        # Scrub the configured credential even if an upstream error echoes it.
        result = json.loads(dumps(result).replace(key, "[secret]"))
        if not isinstance(result, dict) or "result" not in result:
            raise RemoteError("НК вернул ответ без result; операция требует проверки")
        return result["result"]

    def details(self, good_id=None, gtin=None):
        result = self.call("GET", "/v3/feed-product", {"good_id": good_id} if good_id else {"gtin": gtin})
        if not isinstance(result, list) or not result:
            raise RemoteError("НК не вернул собственную карточку товара: проверьте её статус и права",404)
        if good_id:
            matched = [x for x in result if str(x.get("good_id")) == str(good_id)]
            if not matched:
                raise RemoteError("НК вернул другую карточку")
            return matched[0]
        return result[0]


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def pn_attribute(name):
    return re.sub(r"[^\w]","",str(name).lower()) in ("модель","модельартикул","артикул","артикулпроизводителя","pn")


class Engine:
    def __init__(self, store, transport=None):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)
            db.execute("UPDATE settings SET value='2' WHERE key='schema_version'")
        self.path = store.directory / "nk-secrets.json"
        self.client = Client(self.config, transport)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None

    def config(self):
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            saved=self.store.one("SELECT value FROM settings WHERE key='nk_environment'")
            return {"environment":saved["value"]} if saved else {}

    def save_config(self, actor, data):
        with self.lock:
            current = self.config()
            environment = data.get("environment", current.get("environment", "production"))
            if environment not in BASES:
                raise ValueError("Выберите промышленный или тестовый контур НК")
            if current.get("environment", "production") != environment:
                if self.store.one("SELECT id FROM nk_jobs WHERE state NOT IN ('confirmed','failed','cancelled')") or self.store.one("SELECT card_id FROM nk_cards"):
                    raise Conflict("Для смены контура нужна отдельная база; есть связанные карточки или операции")
            key = data.get("api_key", "").strip()
            if key:
                if current.get("api_key") and key!=current["api_key"] and self.store.one("SELECT id FROM nk_jobs WHERE state NOT IN ('confirmed','failed','cancelled')"):
                    raise Conflict("Сначала завершите/сверьте операции. Ключ нельзя менять при незавершённой очереди")
                if len(key) > 1000 or not re.fullmatch(r"[A-Za-z0-9._~+/=\-]+", key):
                    raise ValueError("Некорректный API KEY")
                current["api_key"] = key
            thumb = str(data.get("thumbprint", current.get("thumbprint", ""))).replace(" ", "").upper()
            if thumb and not re.fullmatch(r"[A-F0-9]{40}", thumb):
                raise ValueError("Отпечаток сертификата: 40 шестнадцатеричных символов")
            current.update(environment=environment, thumbprint=thumb, writes_enabled=data.get("writes_enabled") is True)
            if not current.get("signer_token"):
                current["signer_token"] = secrets.token_urlsafe(48)
            temp = self.path.with_suffix(".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                os.fchmod(file.fileno(),0o600)
                file.write(dumps(current))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
            with self.store.transaction() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('nk_environment',?)",(environment,))
                audit(db, actor, "nk_configured", "nk", {"environment": environment, "writes_enabled": current["writes_enabled"], "thumbprint": thumb})

    def status(self):
        config = self.config()
        sync = self.store.one("SELECT * FROM nk_jobs WHERE kind='sync' ORDER BY created DESC LIMIT 1")
        heartbeat = self.store.one("SELECT value FROM settings WHERE key='nk_signer_heartbeat'")
        return {"configured": bool(config.get("api_key")), "environment": config.get("environment", "production"),
                "writes_enabled": config.get("writes_enabled", False), "thumbprint": config.get("thumbprint", ""),
                "sync": {k: sync[k] for k in ("id", "state", "error", "updated")} if sync else None,
                "signer": json.loads(heartbeat["value"]) if heartbeat else None,
                "jobs": self.store.all("SELECT id,kind,card_id,state,error,external_id,created,updated FROM nk_jobs ORDER BY created DESC LIMIT 200")}

    def enqueue(self, actor, kind, payload, card=None):
        key = f"{kind}:{card['id']}:{card['revision']}" if card else kind+":"+uid()
        id = uid()
        with self.store.transaction() as db:
            old = db.execute("SELECT id,state FROM nk_jobs WHERE op_key=? OR op_key LIKE ? ORDER BY created DESC LIMIT 1", (key,key+":attempt:%")).fetchone()
            if old:
                if old["state"]!="failed":
                    return old["id"]
                key += ":attempt:"+uid()
            db.execute("INSERT INTO nk_jobs(id,kind,card_id,revision,actor,op_key,payload,sha256,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (id, kind, card["id"] if card else None, card["revision"] if card else None, actor, key, dumps(payload), digest(payload), "prepared", now(), now()))
            audit(db, actor, "nk_queued", id, {"kind": kind, "card_id": card["id"] if card else None})
        return id

    def sync(self, actor):
        if not self.config().get("api_key"):
            raise ValueError("Сначала настройте API KEY")
        active = self.store.one("SELECT id FROM nk_jobs WHERE kind='sync' AND state NOT IN ('failed','confirmed','cancelled')")
        if active:
            return active["id"]
        return self.enqueue(actor, "sync", {"from": "2000-01-01 00:00:00", "to": (datetime.now(timezone.utc)+timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")})

    def apply_remote(self, raw, actor="nk", card_id=None, force=False):
        good_id = int(raw["good_id"])
        attrs, mapping = {}, {}
        for index, attr in enumerate(raw.get("good_attrs", [])):
            key = f"{attr.get('attr_name', 'Атрибут')} [#{attr['attr_id']}]"
            if key in attrs:
                key += f" · {index}"
            attrs[key] = str(attr.get("attr_value") or "")
            if attr.get("attr_value_type"):
                attrs[key+"::unit"]=str(attr["attr_value_type"])
            mapping[key] = attr
        model = next((str(a.get("attr_value") or "") for a in raw.get("good_attrs",[]) if pn_attribute(a.get("attr_name")) and a.get("attr_value")), "")
        ids = raw.get("identified_by", [])
        gtin = next((str(i["value"]) for i in ids if i.get("type") == "gtin" and i.get("level") == "trade-unit"), "")
        tnved = next((str(a["attr_value"]) for a in raw.get("good_attrs", []) if str(a.get("attr_id")) == "13933"), str(raw.get("tnved", "")))
        cats = raw.get("categories", [])
        statuses = raw.get("good_detailed_status") or [raw.get("good_status", "")]
        label = " / ".join(STATUS.get(s, s) for s in statuses)
        with self.store.transaction() as db:
            existing = db.execute("SELECT card_id FROM nk_cards WHERE good_id=?", (good_id,)).fetchone()
            if existing:
                card_id = existing["card_id"]
            if not card_id:
                match = db.execute("SELECT id FROM cards WHERE external_id=?", (str(good_id),)).fetchone()
                if not match and gtin:
                    match = db.execute("SELECT c.id FROM source_keys s JOIN cards c ON c.id=s.card_id WHERE s.key=? AND c.external_id IS NULL", ("gtin:"+gtin.zfill(14),)).fetchone()
                card_id = match["id"] if match else uid()
            old = db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            snapshot = {"nk": raw, "field_map": mapping, "confirmed_at": now()}
            if old and old["dirty"] and not force:
                # Preserve local values and their base snapshot; keep remote in a
                # separate record for an explicit conflict comparison.
                db.execute("UPDATE cards SET external_status=? WHERE id=?", (label, card_id))
            elif old:
                db.execute("UPDATE cards SET external_id=?,source='nk_api',external_status=?,pn=?,gtin=?,tnved=?,category=?,card_type=?,name=?,attributes=?,source_snapshot=?,revision=revision+1,dirty=0,updated=? WHERE id=?",
                           (str(good_id), label, model or old["pn"], gtin or old["gtin"], tnved if len(tnved)==10 else old["tnved"], cats[0].get("cat_name", "") if cats else "", "set" if raw.get("is_set") else "kit" if raw.get("is_kit") else "unit", raw.get("good_name", ""), dumps(attrs), dumps(snapshot), now(), card_id))
            else:
                db.execute("INSERT INTO cards VALUES(?,?,'nk_api',?,?,?,?,?,?,?,?,?,1,'',0,0,?,?)",
                           (card_id, str(good_id), label, model, gtin, tnved if len(tnved)==10 else "", cats[0].get("cat_name", "") if cats else "", "set" if raw.get("is_set") else "kit" if raw.get("is_kit") else "unit", raw.get("good_name", ""), dumps(attrs), dumps(snapshot), now(), now()))
            db.execute("INSERT OR REPLACE INTO nk_cards VALUES(?,?,?,?,?)", (card_id, good_id, dumps(raw), dumps(mapping), now()))
            db.execute("INSERT OR IGNORE INTO nk_snapshots VALUES(?,?,?,?)",(card_id,digest(raw),dumps(raw),now()))
            audit(db, actor, "nk_snapshot", card_id, {"good_id": good_id, "local_preserved": bool(old and old["dirty"] and not force)})
        return card_id

    def schema(self, cat_id):
        body = self.client.call("GET", "/v3/attributes", {"cat_id": int(cat_id), "attr_type": "a"})
        if not isinstance(body, list):
            raise RemoteError("НК вернул неизвестную структуру атрибутов")
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO nk_schemas VALUES(?,?,?)", (str(cat_id), dumps(body), now()))
        return body

    def prepare(self, actor, kind, card_id, revision, options=None, admin=False):
        options = options or {}
        if kind not in ("save", "moderate", "publish", "gtin"):
            raise ValueError("Неизвестная операция НК")
        config = self.config()
        if not config.get("api_key") or not config.get("writes_enabled"):
            raise ValueError("Администратор должен настроить API и разрешить внешние операции")
        latest_sync=self.store.one("SELECT state,updated FROM nk_jobs WHERE kind='sync' ORDER BY created DESC LIMIT 1")
        if not latest_sync or latest_sync["state"]!="confirmed" or datetime.fromisoformat(latest_sync["updated"])<datetime.now(timezone.utc)-timedelta(minutes=15):
            raise Conflict("Перед внешними действиями завершите свежую синхронизацию всего каталога (не старше 15 минут)")
        with self.lock:
            found = self.store.one("SELECT * FROM cards WHERE id=?", (card_id,))
            if not found:
                raise ValueError("Карточка не найдена")
            card = card_record(found)
            if card["revision"] != revision or card["archived"]:
                raise Conflict("Ревизия изменилась или карточка остановлена")
            if card["owner"] not in (actor, "") and not admin:
                raise Conflict("Карточку взял другой сотрудник")
            current_jobs = self.store.one("SELECT id FROM nk_jobs WHERE card_id=? AND state NOT IN ('confirmed','failed','cancelled')", (card_id,))
            if current_jobs:
                return current_jobs["id"]
            all_cards = self.store.all("SELECT id,pn,gtin,name,external_status FROM cards")
            matches = candidates(card["pn"], card["gtin"], all_cards, card_id)
            if matches and not str(options.get("duplicate_reason", "")).strip():
                raise Conflict("Найдены совпадения PN/GTIN. Просмотрите их и укажите причину продолжения")
            known = self.store.one("SELECT * FROM nk_cards WHERE card_id=?", (card_id,))
            raw = json.loads(known["raw"]) if known else None
            if kind == "gtin":
                if card["gtin"] or not card["pn"]:
                    raise ValueError("Новый GTIN разрешён только для товара с PN без GTIN")
                return self.enqueue(actor, kind, {"quantity": 1, "reason": options.get("duplicate_reason", "")}, card)
            if kind != "save":
                if not raw or card["dirty"]:
                    raise Conflict("Сначала сохраните карточку в НК и подтвердите результат")
                if kind == "publish" and not config.get("thumbprint"):
                    raise ValueError("Настройте сертификат Windows-подписанта")
                return self.enqueue(actor, kind, {"good_id": known["good_id"], "base": digest(raw), "publicationAgreement": options.get("publicationAgreement") is True}, card)
            if raw:
                if raw.get("is_set") or raw.get("is_kit"):
                    raise ValueError("Редактирование комплектов/наборов требует отдельной проверки состава; в этом выпуске доступно чтение и подписание готовых карточек")
                base = card["source_snapshot"].get("nk")
                if not base or digest(base) != digest(raw):
                    raise Conflict("Снимок НК изменился после локальных правок. Сравните версии перед отправкой")
                categories = raw.get("categories", [])
                cat_id = categories[0]["cat_id"] if categories else None
                if card["category"] != (categories[0].get("cat_name", "") if categories else "") or card["card_type"] != "unit":
                    raise ValueError("Изменение категории или типа существующей карточки требует отдельной процедуры НК")
                entry = {"good_id": int(raw["good_id"]), "moderation": 0, "good_attrs": []}
                mapping = json.loads(known["field_map"])
                for key, attr in mapping.items():
                    value = card["attributes"].get(key, str(attr.get("attr_value") or ""))
                    unit=card["attributes"].get(key+"::unit",str(attr.get("attr_value_type") or ""))
                    if pn_attribute(attr.get("attr_name")) and card["pn"] != str(attr.get("attr_value") or ""):
                        value = card["pn"]
                    if value != str(attr.get("attr_value") or "") or unit!=str(attr.get("attr_value_type") or ""):
                        if not value:
                            raise ValueError("Удаление атрибута требует отдельной операции; пустое значение не отправляется: "+key)
                        edit = {k: attr[k] for k in ("attr_id", "attr_value_type", "gtin") if attr.get(k) is not None}
                        edit["attr_value"] = value
                        if unit:
                            edit["attr_value_type"]=unit
                        entry["good_attrs"].append(edit)
                unmapped = [k for k,v in card["attributes"].items() if k not in mapping and v and not k.endswith("::unit")]
                for key in unmapped:
                    match=re.search(r"\[#(\d+)\]",key)
                    if not match:
                        raise ValueError("Поле не сопоставлено со схемой НК: "+key)
                    attribute={"attr_id":int(match[1]),"attr_value":card["attributes"][key]}
                    if card["attributes"].get(key+"::unit"):
                        attribute["attr_value_type"]=card["attributes"][key+"::unit"]
                    entry["good_attrs"].append(attribute)
                if card["name"] != raw.get("good_name", ""):
                    entry["good_name"] = card["name"]
                original_tnved = next((str(a["attr_value"]) for a in raw.get("good_attrs",[]) if str(a.get("attr_id"))=="13933"),str(raw.get("tnved","")))
                if card["tnved"] and len(original_tnved)==10 and card["tnved"]!=original_tnved:
                    raise ValueError("Изменение закреплённого ТН ВЭД требует отдельной процедуры НК")
                if card["gtin"] not in ("", next((str(x.get("value")) for x in raw.get("identified_by", []) if x.get("level")=="trade-unit"), "")):
                    raise ValueError("Нельзя менять закреплённый GTIN карточки НК")
            else:
                if card["card_type"] not in ("", "unit", "Единичный", "Единичный товар"):
                    raise ValueError("Тип товара не подтверждён для создания единичной карточки")
                if not card["pn"] or not card["name"] or not card["tnved"] or not card["gtin"]:
                    raise ValueError("Для создания нужны PN, название, ТН ВЭД и существующий/официально выделенный GTIN")
                scope=self.store.one("SELECT cat_id FROM nk_scopes WHERE card_id=?",(card_id,))
                cat_id = options.get("cat_id") or (scope["cat_id"] if scope else None)
                if not str(cat_id or "").isdigit() or not str(options.get("brand", "")).strip():
                    raise ValueError("Выберите ID категории и укажите бренд для создания")
                entry = {"gtin": card["gtin"], "good_name": card["name"], "tnved": card["tnved"], "brand": options["brand"], "categories": [int(cat_id)],
                         "is_set": False, "is_kit": False, "is_tech_gtin": False, "moderation": 0,
                         "identified_by": [{"value":card["gtin"],"type":"gtin","level":"trade-unit","multiplier":1,"unit":"шт"}], "good_attrs": []}
                for key, value in card["attributes"].items():
                    if not value or key.endswith("::unit"):
                        continue
                    match = re.search(r"\[#(\d+)\]", key)
                    if not match:
                        raise ValueError("Поле без официального ID атрибута: "+key)
                    attr={"attr_id":int(match[1]),"attr_value":value}
                    if card["attributes"].get(key+"::unit"):
                        attr["attr_value_type"]=card["attributes"][key+"::unit"]
                    entry["good_attrs"].append(attr)
            if not cat_id:
                raise ValueError("У карточки отсутствует подтверждённая категория")
            schema = self.schema(cat_id)
            allowed = {str(a["attr_id"]):a for a in schema}
            for attr in entry["good_attrs"]:
                definition = allowed.get(str(attr["attr_id"]))
                if not definition or definition.get("attr_type") == "b":
                    raise ValueError("Атрибут отсутствует в схеме или заблокирован: "+str(attr["attr_id"]))
                if definition.get("attr_preset_only") and definition.get("attr_preset") and attr["attr_value"] not in definition["attr_preset"]:
                    raise ValueError("Значение отсутствует в справочнике НК: "+definition["attr_name"])
                units=definition.get("attr_value_type") or []
                if units and all(isinstance(u,str) for u in units) and attr.get("attr_value_type") not in units:
                    raise ValueError("Выберите единицу измерения НК: "+definition["attr_name"])
                if definition.get("attr_field_type")=="number":
                    try:
                        from decimal import Decimal,InvalidOperation
                        if not Decimal(str(attr["attr_value"])).is_finite():
                            raise ValueError("Не конечное число")
                    except (ValueError,InvalidOperation):
                        raise ValueError("Атрибут должен быть числом: "+definition["attr_name"]) from None
            if not raw:
                present={str(a["attr_id"]) for a in entry["good_attrs"] if str(a.get("attr_value","")).strip()}
                # These top-level feed fields are expanded by NK itself. In
                # particular its documented 10-digit tnved populates 13933.
                if entry.get("good_name"):
                    present.add("2478")
                if entry.get("tnved"):
                    present.update(("3959","13933"))
                present.update(str(a["attr_id"]) for a in schema if a.get("attr_name")=="Товарный знак" and entry.get("brand"))
                missing=[a["attr_name"] for a in schema if a.get("attr_type")=="m" and str(a["attr_id"]) not in present]
                if missing:
                    raise ValueError("Не заполнены обязательные поля НК: "+", ".join(missing))
            if raw:
                # PN edits cannot silently remain local while an unrelated feed
                # is reported as fully applied.
                model=next((str(a.get("attr_value") or "") for a in raw.get("good_attrs",[]) if pn_attribute(a.get("attr_name"))),"")
                model_ids={str(a["attr_id"]) for a in schema if pn_attribute(a.get("attr_name"))}
                if card["pn"]!=model and not any(str(a["attr_id"]) in model_ids and a["attr_value"]==card["pn"] for a in entry["good_attrs"]):
                    raise ValueError("PN не связан с атрибутом модели НК. Загрузите поля категории и укажите модель")
            return self.enqueue(actor, "save", {"entry":entry,"base":digest(raw) if raw else None,"reason":options.get("duplicate_reason","")}, card)

    def state(self, id, state, result=None, error=None, **fields):
        columns = {"state":state,"updated":now(),**fields}
        if result is not None:
            columns["result"] = dumps(result)
        columns["error"] = error
        with self.store.transaction() as db:
            current=db.execute("SELECT state FROM nk_jobs WHERE id=?",(id,)).fetchone()
            if current and current["state"]=="cancelled":
                if result is not None:
                    db.execute("INSERT INTO nk_receipts(job_id,state,body,created) VALUES(?,'cancelled',?,?)",(id,dumps({"late_read_result":result}),now()))
                return
            db.execute("UPDATE nk_jobs SET "+",".join(k+"=?" for k in columns)+" WHERE id=?", (*columns.values(),id))
            if result is not None or error:
                db.execute("INSERT INTO nk_receipts(job_id,state,body,created) VALUES(?,?,?,?)",(id,state,dumps({"result":result,"error":error}),now()))

    def start(self):
        with self.store.transaction() as db:
            db.execute("UPDATE nk_jobs SET state='unknown',error='Сервис прерван при отправке. Только сверка, не повтор' WHERE state='dispatching'")
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.wait(2):
            try:
                self.tick()
            except Exception:
                # Do not leak upstream URLs/credentials in exception logs.
                with self.store.transaction() as db:
                    db.execute("UPDATE nk_jobs SET state='unknown',error='Обработка прервана после намерения отправки. Нужна сверка' WHERE state='dispatching'")
                continue

    def tick(self):
        with self.lock:
            if time.time()<self.client.retry_until:
                return
            jobs = self.store.all("SELECT * FROM nk_jobs WHERE state IN ('prepared','signed') AND next_at<=? ORDER BY created LIMIT 5", (time.time(),))+self.store.all("SELECT * FROM nk_jobs WHERE state IN ('submitted','checking') AND next_at<=? ORDER BY updated LIMIT 5",(time.time(),))
            for job in jobs:
                try:
                    if job["kind"] == "sync":
                        self.sync_tick(job)
                    else:
                        self.operation_tick(job)
                except RemoteError as error:
                    current = self.store.one("SELECT state FROM nk_jobs WHERE id=?", (job["id"],))
                    if current["state"] == "dispatching":
                        rejected=error.code in (400,401,403,404,413,422,429)
                        self.state(job["id"], "failed" if rejected else "unknown", error=str(error))
                    elif job["kind"] == "sync" and error.code in (401,403,404):
                        self.state(job["id"],"failed",error=str(error))
                    elif job["kind"] == "sync" or job["state"] in ("submitted","checking"):
                        self.state(job["id"], "prepared" if job["kind"]=="sync" else job["state"], error=str(error), next_at=time.time()+error.retry)
                    else:
                        current = self.store.one("SELECT state FROM nk_jobs WHERE id=?", (job["id"],))
                        self.state(job["id"], "unknown" if current["state"]=="dispatching" else "failed", error=str(error))
                except (ValueError, KeyError, TypeError) as error:
                    current = self.store.one("SELECT state FROM nk_jobs WHERE id=?", (job["id"],))
                    self.state(job["id"], "unknown" if current["state"]=="dispatching" else "failed", error="Операция остановлена: "+str(error)[:1000])

    def sync_tick(self, job):
        payload = json.loads(job["payload"])
        progress = json.loads(job["result"]) if job["result"] else {"windows":[[payload["from"],payload["to"],0]],"count":0}
        if not progress["windows"]:
            self.state(job["id"], "confirmed", progress)
            return
        start,end,offset = progress["windows"][0]
        try:
            listing = self.client.call("GET", "/v4/product-list", {"from_date":start,"to_date":end,"offset":offset,"limit":25})
        except RemoteError as error:
            if error.code != 413:
                raise
            a,b = datetime.fromisoformat(start),datetime.fromisoformat(end)
            if (b-a).total_seconds()<2:
                raise ValueError("Более 10000 карточек за секунду: требуется выгрузка оператора")
            midpoint = (a+(b-a)/2).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
            progress["windows"][0:1] = [[start,midpoint,0],[midpoint,end,0]]
            self.state(job["id"], "prepared", progress)
            return
        goods = listing.get("goods", [])
        details=self.client.call("GET","/v3/feed-product",{"good_ids":";".join(str(item["good_id"]) for item in goods)}) if goods else []
        if not isinstance(details,list) or {str(item["good_id"]) for item in details}!={str(item["good_id"]) for item in goods}:
            raise RemoteError("НК не вернул полный состав запрошенных карточек. Проверьте права и статусы",404)
        for raw in details:
            self.apply_remote(raw)
        progress["count"] += len(goods)
        if not goods or offset+len(goods) >= int(listing.get("total",offset+len(goods))):
            progress["windows"].pop(0)
        else:
            progress["windows"][0][2] += len(goods)
        self.state(job["id"], "prepared" if progress["windows"] else "confirmed", progress, next_at=time.time()+2)

    def operation_tick(self, job):
        config = self.config()
        payload = json.loads(job["payload"])
        card = self.store.one("SELECT * FROM cards WHERE id=?", (job["card_id"],))
        if job["state"] in ("prepared","signed"):
            if not config.get("writes_enabled"):
                return
            if not card or card["archived"] or card["revision"]!=job["revision"]:
                raise Conflict("Карточка изменилась/остановлена; внешняя отправка запрещена")
            raw = None
            if payload.get("good_id") or payload.get("entry",{}).get("good_id"):
                raw = self.client.details(payload.get("good_id") or payload["entry"]["good_id"])
                if payload.get("base") and digest(raw)!=payload["base"]:
                    raise Conflict("Карточка НК изменилась после подготовки команды")
            statuses = raw.get("good_detailed_status",[]) if raw else []
            if job["kind"] == "publish" and job["state"] == "prepared":
                if "notsigned" not in statuses:
                    raise Conflict("НК не подтвердил статус «Ожидает подписания»")
                result = self.client.call("POST", "/v3/feed-product-document", body={"goodIds":[payload["good_id"]],**({} if raw.get("is_set") else {"publicationAgreement":payload["publicationAgreement"]})})
                xmls = result.get("xmls",[])
                if result.get("errors") or len(xmls)!=1 or str(xmls[0]["goodId"])!=str(payload["good_id"]):
                    raise ValueError("НК не выдал однозначный XML для выбранной карточки")
                content = xmls[0]["xml"].encode("utf-8")
                if len(content)>1024*1024:
                    raise ValueError("XML превышает безопасный лимит подписанта 1 МБ")
                self.state(job["id"], "signing", {"base64Xml":base64.b64encode(content).decode(),"xml_sha256":hashlib.sha256(content).hexdigest(),"goodId":payload["good_id"]})
                return
            if job["kind"] == "moderate" and not any(s in statuses for s in ("draft","errors")):
                raise Conflict("НК не подтвердил редактируемый черновик")
            # Persist intent before each consuming request, including mutating GET.
            with self.store.transaction() as db:
                latest=db.execute("SELECT revision,archived FROM cards WHERE id=?",(job["card_id"],)).fetchone()
                command=db.execute("SELECT state FROM nk_jobs WHERE id=?",(job["id"],)).fetchone()
                if not latest or latest["archived"] or latest["revision"]!=job["revision"] or command["state"] not in ("prepared","signed"):
                    raise Conflict("Карточка остановлена или изменена до отправки")
                db.execute("UPDATE nk_jobs SET state='dispatching',updated=? WHERE id=?",(now(),job["id"]))
            if job["kind"] == "save":
                result = self.client.call("POST", "/v3/feed", body=payload["entry"])
                feed_id = str(result["feed_id"])
                self.state(job["id"], "submitted", result, external_id=feed_id, next_at=time.time()+5)
            elif job["kind"] == "moderate":
                result = self.client.call("GET", "/v3/feed-moderation", {"good_id":payload["good_id"]})
                if result.get("error"):
                    self.state(job["id"], "failed", result, error=str(result["error"]))
                else:
                    self.state(job["id"], "submitted", result, next_at=time.time()+5)
            elif job["kind"] == "gtin":
                result = self.client.call("GET", "/v3/generate-gtins", {"quantity":1})
                drafts = result.get("drafts",[])
                if len(drafts)!=1 or not valid_gtin(str(drafts[0].get("gtin",""))):
                    raise ValueError("НК вернул неоднозначный результат выделения GTIN; повтор запрещён")
                with self.store.transaction() as db:
                    updated=db.execute("UPDATE cards SET gtin=?,revision=revision+1,dirty=1,updated=? WHERE id=? AND gtin=''", (str(drafts[0]["gtin"]),now(),card["id"]))
                    if updated.rowcount!=1:
                        raise Conflict("GTIN выделен, но поле изменилось. Сверьте квитанцию; новый запрос запрещён")
                    audit(db,job["actor"],"nk_gtin_allocated",card["id"],result)
                self.state(job["id"], "confirmed", result)
            elif job["kind"] == "publish":
                signed = json.loads(job["result"])
                result = self.client.call("POST", "/v3/feed-product-sign-pkcs", body=[{"goodId":payload["good_id"],"base64Xml":signed["base64Xml"],"signature":job["signature"]}])
                if payload["good_id"] not in result.get("signed",[]):
                    self.state(job["id"], "failed", result, error="НК не подтвердил принятие подписи")
                else:
                    self.state(job["id"], "submitted", result, next_at=time.time()+5)
            return
        if job["state"] not in ("submitted","checking"):
            return
        if job["kind"] == "save":
            result = self.client.call("GET", "/v3/feed-status", {"feed_id":job["external_id"],"verbose":"true"})
            self.state(job["id"], "submitted", result, next_at=time.time()+15)
            if result.get("status") == "Rejected" or result.get("error_details"):
                self.state(job["id"], "failed", result, error="НК отклонил фид; подробности в квитанции")
                return
            if result.get("status") == "Processing":
                return
            raw = self.client.details(payload["entry"].get("good_id"), payload["entry"].get("gtin"))
            if not self.matches_entry(payload["entry"],raw):
                self.state(job["id"],"submitted",result,error="Фид принят; ожидается подтверждение фактических значений НК",next_at=time.time()+15)
                return
            self.apply_remote(raw,job["actor"],job["card_id"],force=True)
            self.state(job["id"], "confirmed", {"feed":result,"good_id":raw["good_id"]})
        else:
            raw = self.client.details(payload["good_id"])
            self.apply_remote(raw)
            states = raw.get("good_detailed_status",[])
            confirmed = "published" in states and raw.get("good_signed") is True if job["kind"]=="publish" else any(s in states for s in ("moderation","errors","notsigned","published"))
            self.state(job["id"], "confirmed" if confirmed else "submitted", {"good_id":raw["good_id"],"statuses":states}, next_at=time.time()+15)

    def signer_auth(self, token):
        expected = self.config().get("signer_token","")
        return bool(expected and secrets.compare_digest(token,expected))

    def signer_next(self, thumbprint):
        config = self.config()
        if not config.get("thumbprint") or thumbprint!=config["thumbprint"]:
            raise ValueError("Отпечаток сертификата не совпадает")
        with self.lock, self.store.transaction() as db:
            job = db.execute("SELECT j.* FROM nk_jobs j JOIN cards c ON c.id=j.card_id WHERE j.state='signing' AND c.archived=0 AND c.revision=j.revision AND (j.lease_until IS NULL OR j.lease_until<?) ORDER BY j.created LIMIT 1",(time.time(),)).fetchone()
            if not job:
                return {}
            lease = uid()
            db.execute("UPDATE nk_jobs SET lease=?,lease_until=? WHERE id=?", (lease,time.time()+180,job["id"]))
            body = json.loads(job["result"])
            return {"id":job["id"],"kind":"nk_publish","lease":lease,"thumbprint":thumbprint,"detached":True,"sha256":body["xml_sha256"],"payload_base64":body["base64Xml"],"good_id":body["goodId"]}

    def signer_result(self, data):
        with self.lock, self.store.transaction() as db:
            job = db.execute("SELECT * FROM nk_jobs WHERE id=?", (data.get("id"),)).fetchone()
            if not job or job["lease"]!=data.get("lease") or job["state"]!="signing" or job["lease_until"]<time.time():
                raise Conflict("Задание подписи устарело")
            if data.get("thumbprint")!=self.config().get("thumbprint"):
                raise ValueError("Другой сертификат")
            body = json.loads(job["result"])
            if not secrets.compare_digest(str(data.get("sha256","")),body["xml_sha256"]):
                raise ValueError("Hash подписанных байтов не совпадает")
            try:
                signature = base64.b64decode(data.get("signature",""),validate=True)
            except ValueError:
                raise ValueError("Подпись не в формате base64") from None
            if not 64<=len(signature)<=750000:
                raise ValueError("Недопустимый размер подписи")
            try:
                certificates = load_der_pkcs7_certificates(signature)
                fingerprints = [c.fingerprint(hashes.SHA1()).hex().upper() for c in certificates]
            except ValueError:
                raise ValueError("Не удалось разобрать сертификаты CMS-подписи") from None
            if data["thumbprint"] not in fingerprints:
                raise ValueError("В CMS отсутствует настроенный сертификат")
            db.execute("UPDATE nk_jobs SET signature=?,state='signed',updated=? WHERE id=?",(data["signature"],now(),job["id"]))
            audit(db,"signer","nk_signature_received",job["id"],{"sha256":body["xml_sha256"],"thumbprint":data["thumbprint"]})

    @staticmethod
    def matches_entry(entry,raw):
        if entry.get("good_name") and entry["good_name"]!=raw.get("good_name"):
            return False
        for attr in entry.get("good_attrs",[]):
            if not any(str(a.get("attr_id"))==str(attr["attr_id"]) and str(a.get("attr_value"))==str(attr["attr_value"]) and all(str(a.get(k,""))==str(attr[k]) for k in ("gtin","attr_value_type") if k in attr) for a in raw.get("good_attrs",[])):
                return False
        return True

    def reconcile(self,actor,id):
        with self.lock:
            job=self.store.one("SELECT * FROM nk_jobs WHERE id=?",(id,))
            if not job or job["kind"]=="sync":
                raise ValueError("Нет внешней операции для сверки")
            payload=json.loads(job["payload"])
            if job["kind"]=="gtin":
                raise Conflict("Неизвестный исход выделения GTIN: сверить выданные коды в НК вручную. Автоповтор запрещён")
            if job["kind"]=="save" and job["external_id"]:
                self.state(id,"submitted",next_at=0)
            else:
                raw=self.client.details(payload.get("good_id") or payload.get("entry",{}).get("good_id"),payload.get("entry",{}).get("gtin"))
                statuses=raw.get("good_detailed_status",[])
                match=self.matches_entry(payload["entry"],raw) if job["kind"]=="save" else ("published" in statuses and raw.get("good_signed") is True) if job["kind"]=="publish" else any(s in statuses for s in ("moderation","errors","notsigned","published"))
                if not match:
                    raise Conflict("Результат пока не подтверждён НК. Повторная мутация не отправлена")
                self.apply_remote(raw,actor,job["card_id"],force=job["kind"]=="save")
                self.state(id,"confirmed",{"reconciled":True,"good_id":raw["good_id"],"statuses":statuses})
            with self.store.transaction() as db:
                audit(db,actor,"nk_reconciled",id,{"read_only":True})
            return {"ok":True}
