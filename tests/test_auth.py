"""Authentication, first-login enrollment and migration regression tests."""
import hashlib
import sqlite3
import time

from fastapi.testclient import TestClient

from main import create_app
from nk_manager.store import Store, password_hash

ORIGIN = "http://localhost:8020"
HEADERS = {"Origin": ORIGIN}


def sign_in(client, username, password):
    return client.post("/api/login", json={"username": username, "password": password}, headers=HEADERS)


def test_password_enrollment_and_admin_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("NK_PUBLIC_URL", ORIGIN)
    app = create_app(tmp_path / "data")
    app.state.store.setup("admin", "eightletters")
    with TestClient(app, base_url=ORIGIN) as admin:
        response = sign_in(admin, "admin", "eightletters")
        assert response.status_code == 200
        csrf = {**HEADERS, "X-CSRF-Token": response.json()["csrf"]}
        assert response.json()["admin"] is True
        assert sign_in(admin, "admin", "").status_code == 401
        created = admin.post("/api/admin/users", json={"username": "colleague"}, headers=csrf)
        assert created.status_code == 200, created.text
        code = created.json()["temporary_code"]
        assert len(code) >= 8
        assert code not in str(app.state.store.one("SELECT * FROM users WHERE username='colleague'"))
        with TestClient(app, base_url=ORIGIN) as employee:
            assert sign_in(employee, "colleague", "").status_code == 401
            first = sign_in(employee, "colleague", code)
            assert first.status_code == 200
            assert first.json() == {"password_setup_required": True, "username": "colleague"}
            assert employee.get("/api/me").status_code == 401
            for new, repeat in [("short", "short"), ("eightletters", "different")]:
                wrong = employee.post("/api/password/activate", json={
                    "username": "colleague", "code": code,
                    "new_password": new, "repeat_password": repeat}, headers=HEADERS)
                assert wrong.status_code == 422
            activated = employee.post("/api/password/activate", json={
                "username": "colleague", "code": code,
                "new_password": "abcdefgh", "repeat_password": "abcdefgh"}, headers=HEADERS)
            assert activated.status_code == 200, activated.text
            assert activated.json()["admin"] is False
            employee_csrf = {**HEADERS, "X-CSRF-Token": activated.json()["csrf"]}
            assert employee.get("/api/admin/users").status_code == 403
            assert employee.post("/api/admin/users", json={"username": "wrong"}, headers=employee_csrf).status_code == 403
            assert employee.get("/api/me").status_code == 200
            assert sign_in(employee, "colleague", code).status_code == 401
            assert sign_in(employee, "colleague", "abcdefgh").status_code == 200
            reset = admin.post("/api/admin/users/colleague/reset-password", json={}, headers=csrf)
            assert reset.status_code == 200, reset.text
            new_code = reset.json()["temporary_code"]
            assert new_code != code
            assert employee.get("/api/me").status_code == 401
            assert sign_in(employee, "colleague", "abcdefgh").status_code == 401
            assert sign_in(employee, "colleague", new_code).json()["password_setup_required"]
            new_login = employee.post("/api/password/activate", json={
                "username": "colleague", "code": new_code,
                "new_password": "87654321", "repeat_password": "87654321"}, headers=HEADERS)
            assert new_login.status_code == 200


def test_sliding_session_and_password_change(tmp_path, monkeypatch):
    monkeypatch.setenv("NK_PUBLIC_URL", ORIGIN)
    app = create_app(tmp_path / "data")
    app.state.store.setup("admin", "password123")
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        one = sign_in(first, "admin", "password123")
        two = sign_in(second, "admin", "password123")
        assert one.status_code == two.status_code == 200
        cookie = first.cookies.get("nk_session")
        with app.state.store.transaction() as db:
            db.execute("UPDATE sessions SET expires=? WHERE token_hash=?",
                       (time.time() + 3600, hashlib.sha256(cookie.encode()).hexdigest()))
        assert first.get("/api/me").status_code == 200
        expiration = app.state.store.one("SELECT expires FROM sessions WHERE token_hash=?",
                                         (hashlib.sha256(cookie.encode()).hexdigest(),))["expires"]
        assert expiration > time.time() + 13 * 86400
        changed = first.post("/api/password/change", json={
            "current_password": "password123", "new_password": "new-password",
            "repeat_password": "new-password"}, headers={**HEADERS, "X-CSRF-Token": one.json()["csrf"]})
        assert changed.status_code == 200, changed.text
        assert first.get("/api/me").status_code == 200
        assert second.get("/api/me").status_code == 401
        assert sign_in(second, "admin", "password123").status_code == 401
        assert sign_in(second, "admin", "new-password").status_code == 200


def test_legacy_schema_assigns_admin_to_original_setup_only(tmp_path):
    path = tmp_path / "nk-manager.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE users(username TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,
                                  admin INTEGER NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE audit(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,
                               entity TEXT NOT NULL,detail TEXT NOT NULL,created TEXT NOT NULL);
        """)
        db.execute("INSERT INTO settings VALUES('admin_hash',?)", (password_hash("legacy-password"),))
        db.execute("INSERT INTO users VALUES('owner',1)")
        db.execute("INSERT INTO users VALUES('other',1)")
        db.execute("INSERT INTO audit(actor,action,entity,detail,created) VALUES('owner','setup','organization','{}','2026-01-01')")
        db.execute("INSERT INTO sessions VALUES('a','other',1,'csrf',9999999999)")
    store = Store(tmp_path)
    original = store.one("SELECT * FROM users WHERE username='owner'")
    employee = store.one("SELECT * FROM users WHERE username='other'")
    assert original["is_admin"] == 1
    assert original["password_hash"] and original["enrollment_hash"] is None
    assert employee["is_admin"] == 0 and employee["password_hash"] is None
    assert store.one("SELECT count(*) AS n FROM sessions")["n"] == 0
    assert store.one("SELECT value FROM settings WHERE key='schema_version'")["value"] == "2"
    assert Store(tmp_path).one("SELECT is_admin FROM users WHERE username='owner'")["is_admin"] == 1
