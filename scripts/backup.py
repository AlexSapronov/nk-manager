"""Consistent SQLite backup including source files. Refuses to replace a target."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def backup(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_file():
        raise ValueError("Исходная база не найдена")
    if target.exists():
        raise ValueError("Целевой файл уже существует; выберите новый путь")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive file creation reserves the destination without overwriting data.
    with target.open("xb"):
        pass
    target.chmod(0o600)
    try:
        with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or dst.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Проверка целостности не пройдена")
            # Never carry signed-in sessions to a restored copy.
            dst.execute("DELETE FROM sessions")
            dst.commit()
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix(target.suffix+".json").write_text(json.dumps({"sha256": digest, "external_writes": False, "sessions_removed": True}, indent=2)+"\n")
        return digest
    except BaseException:
        target.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup/restore в новый файл; база и исходные XLSX в одном снимке")
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        print("Проверенный снимок:", backup(args.source, args.target))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
