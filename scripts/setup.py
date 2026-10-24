"""Interactive first-run setup. Never accept the admin password as a CLI argument."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nk_manager.store import Store


def main():
    parser = argparse.ArgumentParser(description="Первый запуск менеджера НК")
    parser.add_argument("--data", default=os.environ.get("NK_DATA", str(ROOT / "data")))
    args = parser.parse_args()
    store = Store(args.data)
    if store.one("SELECT 1 FROM settings WHERE key='admin_hash'"):
        print("Уже настроено. Существующие данные и пароль сохранены.")
        return
    username = input("Username сотрудника [alexander]: ").strip() or "alexander"
    password = getpass("Новый пароль администратора (минимум 12 символов): ")
    if password != getpass("Повторите пароль: "):
        raise SystemExit("Пароли различаются. Настройка не выполнена.")
    try:
        store.setup(username, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Готово. Вход сотрудника: {username}, пароль не нужен.")
    print("Вход администратора: тот же username + заданный отдельный пароль.")
    print(f"База: {store.path}")


if __name__ == "__main__":
    main()
