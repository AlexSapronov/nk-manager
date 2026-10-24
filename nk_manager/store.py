import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def uid():
    return secrets.token_hex(16)


def password_hash(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + key).decode()


def verify_password(password, value):
    try:
        raw = base64.b64decode(value, validate=True)
        return hmac.compare_digest(raw[16:], hashlib.scrypt(password.encode(), salt=raw[:16], n=16384, r=8, p=1))
    except (ValueError, TypeError):
        return False


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,admin INTEGER NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT PRIMARY KEY,failures INTEGER NOT NULL,until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY,external_id TEXT UNIQUE,source TEXT NOT NULL,external_status TEXT,
 pn TEXT NOT NULL,gtin TEXT NOT NULL,tnved TEXT NOT NULL,category TEXT NOT NULL,card_type TEXT NOT NULL,name TEXT NOT NULL,
 attributes TEXT NOT NULL,source_snapshot TEXT NOT NULL,revision INTEGER NOT NULL,owner TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,
 dirty INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cards_pn ON cards(pn);
CREATE INDEX IF NOT EXISTS cards_gtin ON cards(gtin);
CREATE TABLE IF NOT EXISTS source_keys(key TEXT PRIMARY KEY,card_id TEXT NOT NULL REFERENCES cards(id));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,entity TEXT NOT NULL,detail TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,content BLOB NOT NULL,actor TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_plans(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,filename TEXT NOT NULL,content BLOB NOT NULL,plan TEXT NOT NULL,actor TEXT NOT NULL,created TEXT NOT NULL,committed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS import_results(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES import_plans(id),results TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,tnved TEXT NOT NULL,category TEXT NOT NULL,card_type TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,
 body TEXT NOT NULL,created TEXT NOT NULL,actor TEXT NOT NULL,approved_by TEXT,UNIQUE(name,tnved,category,card_type,version));
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,kind TEXT NOT NULL,actor TEXT NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL,applied INTEGER NOT NULL DEFAULT 0);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS template_immutable BEFORE UPDATE OF body ON templates WHEN OLD.status!='proposed' BEGIN SELECT RAISE(ABORT,'immutable approved template'); END;
"""


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "nk-manager.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO settings VALUES('schema_version','1')")
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self, query, values=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, values)]

    def one(self, query, values=()):
        rows = self.all(query, values)
        return rows[0] if rows else None

    def setup(self, username, password):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='admin_hash'").fetchone():
                raise ValueError("Приложение уже настроено; существующий пароль не изменён")
            if len(password) < 12:
                raise ValueError("Пароль администратора: минимум 12 символов")
            if not username or len(username) > 64 or not all(c.isalnum() or c in '_-.' for c in username):
                raise ValueError("Username: буквы, цифры, точка, дефис, подчёркивание")
            db.execute("INSERT INTO settings VALUES('admin_hash',?)", (password_hash(password),))
            db.execute("INSERT INTO users(username) VALUES(?)", (username,))
            audit(db, username, "setup", "organization", {"local_only": True})


def audit(db, actor, action, entity, detail):
    db.execute("INSERT INTO audit(actor,action,entity,detail,created) VALUES(?,?,?,?,?)", (actor, action, entity, dumps(detail), now()))
