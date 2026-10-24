"""WSL/local persistent workspace. External NK writes are unavailable by design."""
import hashlib
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
from nk_manager.store import Store, audit, now, uid, verify_password

ROOT = Path(__file__).resolve().parent


def create_app(data_directory=None):
    store = Store(data_directory or os.environ.get("NK_DATA", ROOT / "data"))
    service = Service(store)
    app = FastAPI(title="Менеджер НК", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.service = service
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
    cookie_name = "nk_session"

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 422)

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.hostname not in {urlsplit(origin).hostname for origin in origins}:
            return JSONResponse({"detail": "Недопустимый Host"}, status_code=400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin not in origins:
                return JSONResponse({"detail": "Проверка Origin не пройдена"}, status_code=403)
            if not request.headers.get("content-length", "").isdigit():
                return JSONResponse({"detail": "Требуется Content-Length"}, status_code=411)
        if request.url.path == "/api/import/preview":
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > 21 * 1024 * 1024:
                return JSONResponse({"detail": "Загрузка: нужен Content-Length, максимум 20 МБ файла"}, status_code=413)
        elif request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 3 * 1024 * 1024:
            return JSONResponse({"detail": "Запрос превышает 3 МБ"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def session(request: Request):
        token = request.cookies.get(cookie_name, "")
        saved = store.one("SELECT * FROM sessions WHERE token_hash=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time()))
        if not saved or not store.one("SELECT username FROM users WHERE username=? AND enabled=1", (saved["username"],)):
            raise HTTPException(401, "Войдите в приложение")
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), saved["csrf"]):
            raise HTTPException(403, "Сессия изменилась; обновите страницу")
        return saved

    def admin(user=Depends(session)):
        if not user["admin"]:
            raise HTTPException(403, "Нужна отдельная административная сессия")
        return user

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "commit": commit, "assets": asset_version, "schema": 1, "started": stamp,
                "setup_required": not bool(store.one("SELECT 1 FROM settings WHERE key='admin_hash'")), "nk_api_connected": False, "external_writes": False}

    @app.get("/")
    def index():
        html = (ROOT / "web/index.html").read_text().replace("__ASSETS__", asset_version)
        return Response(html, media_type="text/html")

    @app.post("/api/login")
    async def login(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("username", ""), str) or not isinstance(data.get("admin_password", ""), str):
            raise ValueError("Некорректные данные входа")
        username, password = data.get("username", ""), data.get("admin_password", "")
        address = request.client.host
        with store.transaction() as db:
            attempt = db.execute("SELECT * FROM login_attempts WHERE ip=?", (address,)).fetchone()
            if attempt and attempt["until"] > time.time():
                raise HTTPException(429, "Много попыток входа. Повторите через 5 минут")
            account = db.execute("SELECT 1 FROM users WHERE username=? AND enabled=1", (username,)).fetchone()
            setting = db.execute("SELECT value FROM settings WHERE key='admin_hash'").fetchone()
            ok = bool(account and setting and (not password or verify_password(password, setting["value"])))
            if not ok:
                failures = (attempt["failures"] if attempt and attempt["until"] > time.time()-300 else 0) + 1
                db.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (address, failures, time.time()+300 if failures >= 8 else time.time()))
                # Return here instead of raising: the failed-attempt transaction must commit.
                return JSONResponse({"detail": "Неверный username или пароль администратора. Для первого запуска выполните setup.py"}, status_code=401)
            token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), username, int(bool(password)), csrf, time.time()+8*3600))
            db.execute("DELETE FROM login_attempts WHERE ip=?", (address,))
            audit(db, username, "login", "session", {"admin": bool(password)})
        response = JSONResponse({"username": username, "admin": bool(password), "csrf": csrf})
        response.set_cookie(cookie_name, token, httponly=True, secure=public.scheme == "https", samesite="strict", max_age=8*3600, path="/")
        return response

    @app.get("/api/me")
    def me(user=Depends(session)):
        return {"username": user["username"], "admin": bool(user["admin"]), "csrf": user["csrf"]}

    @app.post("/api/logout")
    def logout(user=Depends(session)):
        with store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (user["token_hash"],))
        response = JSONResponse({"ok": True})
        response.delete_cookie(cookie_name)
        return response

    @app.get("/api/cards")
    def cards(user=Depends(session)):
        return service.cards()

    @app.get("/api/duplicates")
    def duplicates(pn: str = "", gtin: str = "", exclude: str = "", user=Depends(session)):
        return candidates(pn, gtin, service.cards(), exclude)

    @app.post("/api/cards")
    def create(data: dict, user=Depends(session)):
        return service.save(user["username"], data)

    @app.put("/api/cards/{id}")
    def update(id: str, data: dict, user=Depends(session)):
        return service.save(user["username"], data, id, bool(user["admin"]))

    @app.post("/api/cards/{id}/claim")
    def claim(id: str, data: dict, user=Depends(session)):
        return service.claim(user["username"], id, data.get("revision"), bool(user["admin"]), bool(data.get("release")))

    @app.post("/api/cards/{id}/archive")
    def archive(id: str, data: dict, user=Depends(session)):
        return service.archive(user["username"], id, data.get("revision"), str(data.get("reason", "")), bool(data.get("restore")), bool(user["admin"]))

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

    @app.get("/api/export")
    def export(format: str = "zip", ids: str = "", user=Depends(session)):
        xlsx, bundle = service.export(user["username"], ids.split(",") if ids else None)
        is_xlsx = format == "xlsx"
        return Response(xlsx if is_xlsx else bundle, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if is_xlsx else "application/zip",
                        headers={"Content-Disposition": f'attachment; filename="nk-catalog-analysis.{"xlsx" if is_xlsx else "zip"}"'})

    @app.get("/api/templates")
    def templates(user=Depends(session)):
        return store.all("SELECT * FROM templates ORDER BY created DESC")

    @app.post("/api/templates")
    def import_template(data: dict, user=Depends(session)):
        return {"id": service.import_template(user["username"], data)}

    @app.post("/api/templates/{id}/approve")
    def approve(id: str, user=Depends(admin)):
        service.approve_template(user["username"], id)
        return {"ok": True}

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
        return service.apply_template_plan(user["username"], id, str(data.get("duplicate_reason", "")))

    @app.get("/api/admin/users")
    def users(user=Depends(admin)):
        return store.all("SELECT username,enabled FROM users ORDER BY username")

    @app.post("/api/admin/users")
    def add_user(data: dict, user=Depends(admin)):
        username = str(data.get("username", "")).strip()
        if not username or len(username) > 64 or not all(c.isalnum() or c in "_.-" for c in username):
            raise ValueError("Недопустимый username")
        with store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO users(username) VALUES(?)", (username,))
            audit(db, user["username"], "user_created", username, {})
        return {"ok": True}

    @app.get("/api/admin/audit")
    def events(user=Depends(admin)):
        return store.all("SELECT actor,action,entity,detail,created FROM audit ORDER BY id DESC LIMIT 500")

    @app.post("/api/nk/{action}")
    def unavailable(action: str, user=Depends(session)):
        raise HTTPException(503, "Интеграция НК не подключена: нет подтверждённого API-контракта. Ни один запрос в НК не отправлен")

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    return app
