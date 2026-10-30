"""WSL/local persistent workspace. Durable NK API operations and Windows signing broker."""
import hashlib
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
from datetime import datetime, timedelta, timezone
import re
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from nk_manager import __version__
from nk_manager.service import Conflict, Service, candidates
from nk_manager.store import Store, audit, dumps, now, uid, verify_password, password_hash
from nk_manager.nk import Engine

ROOT = Path(__file__).resolve().parent


def create_app(data_directory=None):
    store = Store(data_directory or os.environ.get("NK_DATA", ROOT / "data"))
    service = Service(store)
    nk = Engine(store)
    from nk_manager.presets import install
    from nk_manager.nk import recover_missing_pn
    install(store)
    recover_missing_pn(store)
    @asynccontextmanager
    async def lifespan(app):
        nk.start()
        try:
            yield
        finally:
            nk.stop.set()
            if nk.thread:
                await run_in_threadpool(nk.thread.join,50)
            nk.client.close()

    app = FastAPI(title="Менеджер НК", docs_url=None, redoc_url=None, openapi_url=None,lifespan=lifespan)
    app.state.store = store
    app.state.service = service
    app.state.nk = nk

    stamp = now()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    asset_version = hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "web").glob("*")) if p.is_file())).hexdigest()[:16]
    configured = os.environ.get("NK_PUBLIC_URL", "http://localhost:8020").rstrip("/")
    public = urlsplit(configured)
    if public.scheme not in ("http", "https") or not public.netloc:
        raise ValueError("NK_PUBLIC_URL должен быть полным http(s) адресом")
    origins = {configured}
    if public.hostname in ("localhost", "127.0.0.1"):
        port = public.port or (443 if public.scheme == "https" else 80)
        origins |= {f"{public.scheme}://localhost:{port}", f"{public.scheme}://127.0.0.1:{port}"}
    # For a Windows signer connected through an SSH tunnel, allow loopback Host
    # only on its Bearer-authenticated API. All UI endpoints retain strict Host checks.
    allow_signer_tunnel = os.environ.get("NK_SIGNER_ALLOW_LOOPBACK_HOST", "0") == "1"
    cookie_name = "nk_session"
    session_lifetime = 14 * 24 * 3600
    refresh_window = 7 * 24 * 3600

    def set_session_cookie(response, token):
        response.set_cookie(cookie_name, token, httponly=True, secure=public.scheme == "https",
                            samesite="lax", max_age=session_lifetime, path="/")

    def issue_session(db, username, is_admin):
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), username, int(is_admin),
                    csrf, time.time() + session_lifetime))
        audit(db, username, "login", "session", {"admin": bool(is_admin)})
        return token, csrf

    def password_from_form(data):
        password, repeated = data.get("new_password"), data.get("repeat_password")
        if not isinstance(password, str) or not isinstance(repeated, str):
            raise ValueError("Введите новый пароль и его повтор")
        if len(password) < 8:
            raise ValueError("Пароль должен содержать минимум 8 символов")
        if len(password) > 1024:
            raise ValueError("Пароль слишком длинный")
        if password != repeated:
            raise ValueError("Пароли не совпадают")
        return password

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 422)

    @app.middleware("http")
    async def security(request, call_next):
        signer_loopback_host = (
            allow_signer_tunnel
            and request.url.path.startswith("/api/signer/")
            and request.url.hostname in {"localhost", "127.0.0.1"}
        )
        if request.url.hostname not in {urlsplit(origin).hostname for origin in origins} and not signer_loopback_host:
            return JSONResponse({"detail": "Недопустимый Host"}, status_code=400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if not request.url.path.startswith("/api/signer/") and origin not in origins:
                return JSONResponse({"detail": "Проверка Origin не пройдена"}, status_code=403)
            if not request.headers.get("content-length", "").isdigit():
                return JSONResponse({"detail": "Требуется Content-Length"}, status_code=411)
        if request.url.path in ("/api/import/preview", "/api/admin/creation-batches/preview", "/api/admin/recovery-batches/preview"):
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > 21 * 1024 * 1024:
                return JSONResponse({"detail": "Загрузка: нужен Content-Length, максимум 20 МБ файла"}, status_code=413)
        elif request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 3 * 1024 * 1024:
            return JSONResponse({"detail": "Запрос превышает 3 МБ"}, status_code=413)
        # Freeze business mutations while a verified full snapshot is being staged.
        # Credentials, sessions and audit remain available.
        if request.method not in ("GET","HEAD","OPTIONS") and request.url.path.startswith("/api/"):
            from nk_manager.rebuild import running
            if running(store) and request.url.path not in ("/api/login","/api/logout","/api/password/change","/api/password/activate","/api/admin/nk/rebuild"):
                return JSONResponse({"detail":"Идёт полная перестройка каталога НК. Изменения временно заблокированы до завершения загрузки."},status_code=409)
        response = await call_next(request)
        if getattr(request.state, "renew_session", False):
            token = request.cookies.get(cookie_name, "")
            if store.one("SELECT 1 FROM sessions WHERE token_hash=? AND expires>?",
                         (hashlib.sha256(token.encode()).hexdigest(), time.time())):
                set_session_cookie(response, token)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def session(request: Request):
        token = request.cookies.get(cookie_name, "")
        if not token:
            raise HTTPException(401, "Войдите в приложение")
        saved = store.one("""SELECT s.*, u.is_admin FROM sessions s
                             JOIN users u ON u.username=s.username
                             WHERE s.token_hash=? AND s.expires>?
                               AND u.enabled=1 AND u.password_hash IS NOT NULL""",
                          (hashlib.sha256(token.encode()).hexdigest(), time.time()))
        if not saved:
            raise HTTPException(401, "Войдите в приложение")
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), saved["csrf"]):
            raise HTTPException(403, "Сессия изменилась; обновите страницу")
        if saved["expires"] < time.time() + refresh_window:
            with store.transaction() as db:
                db.execute("UPDATE sessions SET expires=? WHERE token_hash=? AND expires>?",
                           (time.time() + session_lifetime, saved["token_hash"], time.time()))
            request.state.renew_session = True
        return saved

    def admin(user=Depends(session)):
        if not user["is_admin"]:
            raise HTTPException(403, "Недостаточно прав администратора")
        return user

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "commit": commit, "assets": asset_version, "schema": int(store.one("SELECT value FROM settings WHERE key='schema_version'")["value"]), "started": stamp,
                "setup_required": not bool(store.one("SELECT 1 FROM settings WHERE key='admin_hash'")), "nk_api_configured": nk.status()["configured"], "external_writes": nk.status()["writes_enabled"]}

    @app.get("/")
    def index():
        html = (ROOT / "web/index.html").read_text().replace("__ASSETS__", asset_version)
        return Response(html, media_type="text/html")

    @app.post("/api/login")
    async def login(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("username"), str) or not isinstance(data.get("password"), str):
            raise ValueError("Некорректные данные входа")
        username, password = data["username"].strip(), data["password"]
        address = (request.client.host if request.client else "unknown") + ":" + username.casefold()
        with store.transaction() as db:
            attempt = db.execute("SELECT * FROM login_attempts WHERE ip=?", (address,)).fetchone()
            if attempt and attempt["until"] > time.time():
                raise HTTPException(429, "Много попыток входа. Повторите через 5 минут")
            account = db.execute("SELECT * FROM users WHERE username=? AND enabled=1", (username,)).fetchone()
            enrolled = bool(account and account["enrollment_hash"] and password and
                            verify_password(password, account["enrollment_hash"]))
            valid = bool(account and account["password_hash"] and password and
                         verify_password(password, account["password_hash"]))
            if not valid and not enrolled:
                failures = (attempt["failures"] if attempt and attempt["until"] > time.time()-300 else 0) + 1
                db.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)",
                           (address, failures, time.time()+300 if failures >= 8 else time.time()))
                return JSONResponse({"detail": "Неверный логин, пароль или одноразовый код"}, status_code=401)
            db.execute("DELETE FROM login_attempts WHERE ip=?", (address,))
            if enrolled:
                return {"password_setup_required": True, "username": username}
            token, csrf = issue_session(db, username, account["is_admin"])
        response = JSONResponse({"username": username, "admin": bool(account["is_admin"]), "csrf": csrf})
        set_session_cookie(response, token)
        return response

    @app.post("/api/password/activate")
    async def activate_password(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("username"), str) or not isinstance(data.get("code"), str):
            raise ValueError("Некорректные данные активации")
        new_password = password_from_form(data)
        username, code = data["username"].strip(), data["code"]
        address = (request.client.host if request.client else "unknown") + ":" + username.casefold()
        with store.transaction() as db:
            attempt = db.execute("SELECT * FROM login_attempts WHERE ip=?", (address,)).fetchone()
            if attempt and attempt["until"] > time.time():
                raise HTTPException(429, "Много попыток входа. Повторите через 5 минут")
            account = db.execute("SELECT * FROM users WHERE username=? AND enabled=1", (username,)).fetchone()
            if not account or not account["enrollment_hash"] or not code or not verify_password(code, account["enrollment_hash"]):
                failures = (attempt["failures"] if attempt and attempt["until"] > time.time()-300 else 0) + 1
                db.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)",
                           (address, failures, time.time()+300 if failures >= 8 else time.time()))
                return JSONResponse({"detail": "Неверный или уже использованный одноразовый код"}, status_code=401)
            db.execute("UPDATE users SET password_hash=?, enrollment_hash=NULL WHERE username=?",
                       (password_hash(new_password), username))
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            db.execute("DELETE FROM login_attempts WHERE ip=?", (address,))
            audit(db, username, "password_set", username, {"first_login": True})
            token, csrf = issue_session(db, username, account["is_admin"])
        response = JSONResponse({"username": username, "admin": bool(account["is_admin"]), "csrf": csrf})
        set_session_cookie(response, token)
        return response

    @app.post("/api/password/change")
    def change_password(data: dict, user=Depends(session)):
        old = data.get("current_password")
        if not isinstance(old, str):
            raise ValueError("Укажите действующий пароль")
        new_password = password_from_form(data)
        with store.transaction() as db:
            account = db.execute("SELECT password_hash FROM users WHERE username=?", (user["username"],)).fetchone()
            if not account or not verify_password(old, account["password_hash"]):
                raise HTTPException(403, "Неверный действующий пароль")
            db.execute("UPDATE users SET password_hash=?, enrollment_hash=NULL WHERE username=?",
                       (password_hash(new_password), user["username"]))
            db.execute("DELETE FROM sessions WHERE username=? AND token_hash!=?",
                       (user["username"], user["token_hash"]))
            audit(db, user["username"], "password_changed", user["username"], {})
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(session)):
        return {"username": user["username"], "admin": bool(user["is_admin"]), "csrf": user["csrf"]}

    @app.post("/api/logout")
    def logout(user=Depends(session)):
        with store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (user["token_hash"],))
        response = JSONResponse({"ok": True})
        response.delete_cookie(cookie_name, path="/", samesite="lax", secure=public.scheme == "https")
        return response

    @app.get("/api/cards")
    def cards(user=Depends(session)):
        return service.cards()

    @app.get("/api/cards/summary")
    def cards_summary(user=Depends(session)):
        return service.summaries()

    @app.get("/api/cards/{id}")
    def get_card(id: str,user=Depends(session)):
        return service.get(id)

    @app.get("/api/workspace/version")
    def workspace_version(user=Depends(session)):
        """Cheap invalidation token, not a full cards/workspace serialization."""
        return {"version": ":".join(map(str, (
            store.one("SELECT COALESCE(MAX(id),0) AS value FROM audit")["value"],
            store.one("SELECT COALESCE(MAX(updated),'') AS value FROM nk_jobs WHERE kind NOT IN ('sync','rebuild')")["value"],
            store.one("SELECT COUNT(*) AS value FROM cards")["value"]
        )))}

    @app.get("/api/duplicates")
    def duplicates(pn: str = "", gtin: str = "", exclude: str = "", user=Depends(session)):
        return candidates(pn, gtin, service.cards(), exclude)

    @app.post("/api/cards")
    def create(data: dict, user=Depends(session)):
        return service.save(user["username"], data)

    @app.put("/api/cards/{id}")
    def update(id: str, data: dict, user=Depends(session)):
        return service.save(user["username"], data, id, bool(user["is_admin"]))

    @app.post("/api/cards/{id}/claim")
    def claim(id: str, data: dict, user=Depends(session)):
        return service.claim(user["username"], id, data.get("revision"), bool(user["is_admin"]), bool(data.get("release")))

    @app.post("/api/cards/{id}/archive")
    def archive(id: str, data: dict, user=Depends(session)):
        return service.archive(user["username"], id, data.get("revision"), str(data.get("reason", "")), bool(data.get("restore")), bool(user["is_admin"]))

    @app.get("/api/cards/{id}/history")
    def history(id: str, user=Depends(session)):
        return store.all("SELECT actor,action,detail,created FROM audit WHERE entity=? ORDER BY id DESC LIMIT 200", (id,))

    @app.post("/api/import/preview")
    async def preview(file: UploadFile = File(...), sheet: str = Form(""), header: int = Form(1), mapping: str = Form(""), defaults: str = Form("{}"), purpose: str = Form("catalog"), user=Depends(session)):
        if purpose not in ("catalog", "new"):
            raise ValueError("Неизвестный вид импорта")
        if not str(file.filename).lower().endswith(".xlsx"):
            raise ValueError("Нужен файл XLSX")
        content = await file.read(20 * 1024 * 1024 + 1)
        await file.close()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, "Файл превышает 20 МБ")
        try:
            columns = json.loads(mapping) if mapping else None
            explicit_defaults = json.loads(defaults)
        except json.JSONDecodeError as exc:
            raise ValueError("Некорректное сопоставление") from exc
        if columns is not None and not isinstance(columns, dict):
            raise ValueError("Сопоставление должно быть объектом")
        filename = str(file.filename).replace("\\", "/").split("/")[-1][:200]
        # XLSX parsing is synchronous; run in the thread pool, not the event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(service.preview_import, user["username"], content, filename, sheet or None, header, columns, purpose, explicit_defaults)

    @app.post("/api/import/{id}/commit")
    def commit_import(id: str, data: dict, user=Depends(session)):
        rows = data.get("rows")
        if data.get("confirmed") is not True or not isinstance(rows, list) or len(rows) > 20000 or any(type(row) is not int for row in rows):
            raise ValueError("Требуется подтверждение выбранных строк")
        return service.commit_import(user["username"], id, data["rows"], str(data.get("duplicate_reason", "")))

    @app.get("/api/imports")
    def imports(user=Depends(session)):
        return store.all("SELECT i.id,i.filename,i.created,i.actor,r.results FROM imports i JOIN import_results r ON r.plan_id=i.id ORDER BY i.created DESC")

    @app.get("/api/admin/creation-batches")
    def creation_batches(user=Depends(admin)):
        return store.all('SELECT id,filename,state,created FROM nk_creation_batches ORDER BY created DESC LIMIT 100')

    @app.post("/api/admin/creation-batches/preview")
    async def preview_creation_batch(file: UploadFile = File(...), user=Depends(admin)):
        if not str(file.filename).lower().endswith('.xlsx'):
            raise ValueError('Нужен файл XLSX')
        content=await file.read(20*1024*1024+1)
        await file.close()
        if len(content)>20*1024*1024:raise HTTPException(413,'Файл превышает 20 МБ')
        from nk_manager.bulk_creation import preview
        filename=str(file.filename).replace('\\','/').split('/')[-1][:200]
        return await run_in_threadpool(preview,nk,user['username'],content,filename)

    @app.get("/api/admin/creation-batches/{id}")
    def creation_batch(id: str,user=Depends(admin)):
        from nk_manager.bulk_creation import snapshot
        return snapshot(store,id)

    @app.post("/api/admin/creation-batches/{id}/start")
    def start_creation_batch(id: str,data: dict,user=Depends(admin)):
        if data.get('confirmed') is not True:raise ValueError('Подтвердите создание карточек и выделение GTIN')
        from nk_manager.bulk_creation import start
        return start(nk,user['username'],id,data.get('kind_choices',{}))

    @app.get("/api/admin/creation-batches/{id}/export")
    def export_creation_batch(id: str,user=Depends(admin)):
        from nk_manager.bulk_creation import export
        return Response(export(store,id),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="nk-cards-gtin.xlsx"'})

    @app.get("/api/admin/recovery-batches")
    def recovery_batches(user=Depends(admin)):
        return store.all("SELECT id,filename,state,created FROM nk_recovery_batches ORDER BY created DESC LIMIT 100")

    @app.post("/api/admin/recovery-batches/preview")
    async def recovery_preview(file: UploadFile = File(...), user=Depends(admin)):
        if not str(file.filename).lower().endswith('.xlsx'):
            raise ValueError('Нужен файл XLSX')
        content = await file.read(20*1024*1024+1)
        await file.close()
        if len(content)>20*1024*1024:
            raise HTTPException(413,'Файл превышает 20 МБ')
        from nk_manager.gtin_recovery import preview
        filename=str(file.filename).replace('\\','/').split('/')[-1][:200]
        return await run_in_threadpool(preview,nk,user['username'],content,filename)

    @app.get("/api/admin/recovery-batches/{id}")
    def recovery_batch(id: str,user=Depends(admin)):
        from nk_manager.gtin_recovery import snapshot
        return snapshot(store,id)

    @app.post("/api/admin/recovery-batches/{id}/start")
    def recovery_start(id: str,data: dict,user=Depends(admin)):
        if data.get('confirmed') is not True:
            raise ValueError('Подтвердите сохранение черновиков в Национальном каталоге')
        from nk_manager.gtin_recovery import start
        return start(nk,user['username'],id,data.get('kind_choices',{}),data.get('manufacturer_as_brand') is True)

    @app.get("/api/admin/recovery-batches/{id}/export")
    def recovery_export(id: str,user=Depends(admin)):
        from nk_manager.gtin_recovery import export
        return Response(export(store,id),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="nk-gtin-recovery.xlsx"'})

    @app.get("/api/export")
    def export(format: str = "zip", ids: str = "", user=Depends(session)):
        xlsx, bundle = service.export(user["username"], ids.split(",") if ids else None)
        is_xlsx = format == "xlsx"
        return Response(xlsx if is_xlsx else bundle, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if is_xlsx else "application/zip",
                        headers={"Content-Disposition": f'attachment; filename="nk-catalog-analysis.{"xlsx" if is_xlsx else "zip"}"'})

    @app.get("/api/creation/catalog")
    def creation_catalog(user=Depends(session)):
        from nk_manager.creation import catalog
        return {'kinds':catalog(store)}

    @app.post("/api/creation/preview")
    def creation_preview(data: dict, user=Depends(session)):
        from nk_manager.creation import draft
        return draft(store,data)

    @app.get("/api/templates")
    def templates(user=Depends(session)):
        from nk_manager.presets import installed
        builtin_ids = installed(store)
        return [{**t,"builtin":t["id"] in builtin_ids} for t in store.all("SELECT * FROM templates ORDER BY created DESC")]

    @app.post("/api/templates")
    def import_template(data: dict, user=Depends(session)):
        return {"id": service.import_template(user["username"], data)}

    @app.post("/api/templates/{id}/approve")
    def approve(id: str, user=Depends(admin)):
        service.approve_template(user["username"], id)
        return {"ok": True}

    @app.get("/api/drafts/workspace")
    def draft_workspace(user=Depends(session)):
        from nk_manager.draft_workflow import workspace
        return workspace(service,user['username'])

    @app.post("/api/drafts/submit")
    def draft_submit(data: dict,user=Depends(session)):
        from nk_manager.draft_workflow import submit
        ids=data.get('ids')
        if data.get('confirmed') is not True or not isinstance(ids,list) or not ids or len(ids)>1000 or any(not isinstance(i,str) for i in ids):
            raise ValueError('Выберите карточки и подтвердите отправку на модерацию')
        revisions=data.get('revisions')
        if not isinstance(revisions,dict) or any(type(revisions.get(i)) is not int for i in ids):raise ValueError('Обновите список карточек перед отправкой')
        return submit(nk,user['username'],ids,bool(user['admin']),data.get('allow_duplicates') is True,revisions)

    @app.post("/api/normalization/preview")
    def normalization_preview(data: dict,user=Depends(session)):
        from nk_manager.normalization_service import preview
        ids=data.get('ids')
        if ids is not None and (not isinstance(ids,list) or len(ids)>10000 or any(not isinstance(i,str) for i in ids)):
            raise ValueError('Выберите до 10000 карточек')
        return preview(service,user['username'],ids)

    @app.post("/api/normalization/{id}/apply")
    def normalization_apply(id: str,data: dict,user=Depends(session)):
        from nk_manager.normalization_service import apply
        if data.get('confirmed') is not True:raise ValueError('Подтвердите изменения из предпросмотра')
        return apply(service,user['username'],id,data.get('ids'),str(data.get('duplicate_reason') or ''))

    @app.post("/api/template-plan")
    def template_plan(data: dict, user=Depends(session)):
        ids = data.get("ids")
        if not isinstance(ids, list) or len(ids) > 1000 or any(not isinstance(id, str) or len(id) > 64 for id in ids):
            raise ValueError("Выберите до 1000 карточек")
        return service.template_plan(user["username"], ids)

    @app.post("/api/template-plan/{id}/apply")
    def apply_plan(id: str, data: dict, user=Depends(session)):
        if data.get("confirmed") is not True:
            raise ValueError("Нужно явное подтверждение")
        return service.apply_template_plan(user["username"], id, str(data.get("duplicate_reason", "")), data.get("ids"))

    @app.get("/api/admin/users")
    def users(user=Depends(admin)):
        return store.all("""SELECT username, enabled, is_admin,
                            CASE WHEN password_hash IS NULL THEN 0 ELSE 1 END AS password_set
                            FROM users ORDER BY username""")

    @app.post("/api/admin/users")
    def add_user(data: dict, user=Depends(admin)):
        username = str(data.get("username", "")).strip()
        if not username or len(username) > 64 or not all(c.isalnum() or c in "_.-" for c in username):
            raise ValueError("Недопустимый username")
        code = secrets.token_urlsafe(18)
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("Этот username уже существует")
            db.execute("INSERT INTO users(username,enrollment_hash) VALUES(?,?)",
                       (username, password_hash(code)))
            audit(db, user["username"], "user_created", username, {"enrollment_required": True})
        return {"ok": True, "username": username, "temporary_code": code}

    @app.post("/api/admin/users/{username}/reset-password")
    def reset_user_password(username: str, user=Depends(admin)):
        code = secrets.token_urlsafe(18)
        with store.transaction() as db:
            if not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise HTTPException(404, "Пользователь не найден")
            db.execute("UPDATE users SET password_hash=NULL, enrollment_hash=? WHERE username=?",
                       (password_hash(code), username))
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            audit(db, user["username"], "password_reset", username, {})
        return {"ok": True, "username": username, "temporary_code": code}

    @app.get("/api/admin/audit")
    def events(user=Depends(admin)):
        return store.all("SELECT actor,action,entity,detail,created FROM audit ORDER BY id DESC LIMIT 500")

    @app.get("/api/nk/status")
    def nk_status(user=Depends(session)):
        return nk.status()

    @app.post("/api/admin/nk/config")
    def nk_config(data: dict, user=Depends(admin)):
        nk.save_config(user["username"],data)
        return nk.status()

    @app.post("/api/admin/nk/test")
    def nk_test(user=Depends(admin)):
        result = nk.client.call("GET", "/v4/product-list", {"limit":1,"offset":0,"from_date":(datetime.now(timezone.utc)-timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),"to_date":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")})
        return {"ok":True,"total":result.get("total"),"returned":len(result.get("goods",[]))}

    @app.post("/api/admin/nk/signer-token")
    def nk_signer_token(user=Depends(admin)):
        with store.transaction() as db:
            audit(db,user["username"],"signer_token_viewed","nk",{})
        return {"token":nk.config().get("signer_token","")}

    @app.post("/api/nk/sync")
    def nk_sync(data: dict = {}, user=Depends(session)):
        return {"id":nk.sync(user["username"],full=data.get("full") is True)}

    @app.get("/api/admin/nk/rebuild")
    def rebuild_status(user=Depends(admin)):
        from nk_manager.rebuild import status
        return status(store) or {"state":"never"}

    @app.post("/api/admin/nk/rebuild")
    def rebuild_begin(data: dict,user=Depends(admin)):
        if (data.get("confirmed") is not True or data.get("discard_local_changes") is not True
            or data.get("confirmation") != "СБРОСИТЬ"):
            raise ValueError('Подтвердите удаление локальных правок и введите СБРОСИТЬ')
        from nk_manager.rebuild import begin
        return {"job_id":begin(nk,user["username"])}

    @app.get("/api/nk/categories")
    def nk_categories(tnved: str, user=Depends(session)):
        if not re.fullmatch(r"\d{4,10}",tnved):
            raise ValueError("Введите ТН ВЭД от 4 до 10 цифр")
        return nk.client.call("GET","/v3/categories",{"tnved":tnved})

    @app.get("/api/nk/field-profile")
    def nk_field_profile(tnved: str = "", user=Depends(session)):
        from nk_manager.fields import published_fields
        return published_fields(store, tnved.strip())

    @app.get("/api/nk/schema/{cat_id}")
    def nk_schema(cat_id: int, user=Depends(session)):
        # Explicit user refresh always obtains the current official schema.
        return nk.schema(cat_id,force=True)

    @app.post("/api/nk/prepare/{kind}")
    def nk_prepare(kind: str, data: dict, user=Depends(session)):
        if data.get("confirmed") is not True:
            raise ValueError("Нужно явное подтверждение внешнего действия")
        return {"id":nk.prepare(user["username"],kind,str(data.get("id","")),data.get("revision"),data.get("options") or {},bool(user["is_admin"]))}

    @app.get("/api/nk/job/{id}")
    def nk_job(id: str, user=Depends(session)):
        job=store.one("SELECT id,kind,card_id,state,result,error,external_id,created,updated FROM nk_jobs WHERE id=?",(id,))
        if not job:
            raise HTTPException(404,"Операция не найдена")
        if job["kind"]=="publish" and job["state"] in ("signing","signed"):
            job["result"] = "Подготовлен неизменяемый XML, ожидается подпись/отправка"
        return job

    @app.post("/api/nk/job/{id}/check")
    def nk_check(id: str, user=Depends(admin)):
        return nk.reconcile(user["username"],id)

    @app.get("/api/nk/card/{id}")
    def nk_card(id: str,user=Depends(session)):
        saved=store.one("SELECT * FROM nk_cards WHERE card_id=?",(id,))
        scope=store.one("SELECT cat_id FROM nk_scopes WHERE card_id=?",(id,))
        raw=json.loads(saved["raw"]) if saved else None
        cat_id=scope["cat_id"] if scope else str(raw["categories"][0]["cat_id"]) if raw and raw.get("categories") else None
        schema=store.one("SELECT body FROM nk_schemas WHERE cat_id=?",(cat_id,)) if cat_id else None
        return {"raw":raw,"confirmed":saved["confirmed"] if saved else None,"cat_id":cat_id,"schema":json.loads(schema["body"]) if schema else []}

    @app.post("/api/nk/card/{id}/category")
    def nk_card_category(id: str,data: dict,user=Depends(session)):
        card=service.get(id)
        if card["revision"]!=data.get("revision") or card["owner"] not in ("",user["username"]) and not user["is_admin"]:
            raise Conflict("Ревизия/ответственность изменились")
        cat_id=str(data.get("cat_id",""))
        if not cat_id.isdigit() or not store.one("SELECT cat_id FROM nk_schemas WHERE cat_id=?",(cat_id,)):
            raise ValueError("Сначала загрузите реальную схему выбранной категории")
        with store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO nk_scopes VALUES(?,?)",(id,cat_id))
            audit(db,user["username"],"nk_category_selected",id,{"cat_id":cat_id})
        return {"ok":True}

    @app.post("/api/nk/card/{id}/refresh")
    def nk_card_refresh(id: str,data: dict,user=Depends(session)):
        if data.get("confirmed") is not True:
            raise ValueError("Подтвердите замену локальных полей актуальными данными НК")
        with nk.lock:
            card=service.get(id)
            if card["revision"]!=data.get("revision") or card["owner"] not in ("",user["username"]) and not user["is_admin"]:
                raise Conflict("Ревизия или ответственный изменились")
            if store.one("SELECT id FROM nk_jobs WHERE card_id=? AND state NOT IN ('failed','confirmed','cancelled')",(id,)):
                raise Conflict("Сначала завершите или сверьте операцию НК")
            saved=store.one("SELECT good_id FROM nk_cards WHERE card_id=?",(id,))
            if not saved:
                raise ValueError("Карточка пока не связана с API НК")
            raw=nk.client.details(saved["good_id"])
            with store.transaction() as db:
                audit(db,user["username"],"nk_local_copy_replaced",id,{"previous_local":card})
            nk.apply_remote(raw,user["username"],id,force=True)
        return service.get(id)

    def signer(request: Request):
        supplied=request.headers.get("authorization","").removeprefix("Bearer ")
        if not nk.signer_auth(supplied):
            raise HTTPException(403,"Подписант не авторизован")

    @app.post("/api/signer/heartbeat",dependencies=[Depends(signer)])
    def heartbeat(data: dict):
        if data.get("thumbprint")!=nk.config().get("thumbprint"):
            raise ValueError("Другой сертификат")
        with store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('nk_signer_heartbeat',?)",(dumps({"thumbprint":data.get("thumbprint"),"expires":data.get("expires"),"status":str(data.get("status",""))[:200],"time":now()}),))
        return {"ok":True}

    @app.post("/api/signer/next",dependencies=[Depends(signer)])
    def signer_next():
        return nk.signer_next(nk.config().get("thumbprint",""))

    @app.post("/api/signer/result",dependencies=[Depends(signer)])
    def signer_result(data: dict):
        nk.signer_result(data)
        return {"ok":True}

    @app.post("/api/nk/{action}")
    def legacy_nk_action(action: str,user=Depends(session)):
        raise HTTPException(503,"Используйте подготовку и подтверждение операции в интерфейсе карточки НК")

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    return app
