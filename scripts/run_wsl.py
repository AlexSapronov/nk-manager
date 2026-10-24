"""Loopback-only Uvicorn launcher; avoids port collisions with other projects."""
import argparse
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nk_manager.store import Store


def main():
    parser = argparse.ArgumentParser(description="Запуск реального локального менеджера НК в WSL2")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--data", default=os.environ.get("NK_DATA", str(ROOT / "data")))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("Укажите порт 1024–65535")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", args.port))
        except OSError:
            raise SystemExit(f"Порт {args.port} занят. Ничего не остановлено. Повторите с --port {args.port+1}.")
    directory = str(Path(args.data).resolve())
    if not Store(directory).one("SELECT 1 FROM settings WHERE key='admin_hash'"):
        raise SystemExit("Сначала выполните .venv/bin/python scripts/setup.py (с тем же --data, если меняли путь).")
    os.environ["NK_DATA"] = directory
    os.environ["NK_PUBLIC_URL"] = f"http://localhost:{args.port}"
    print(f"Откройте в Windows: http://localhost:{args.port}", flush=True)
    print("Постоянная БД. API НК настраивается в админке; Windows-подписант запускается отдельно. Остановка: Ctrl+C.", flush=True)
    os.chdir(ROOT)
    import uvicorn
    uvicorn.run("main:create_app", factory=True, host="127.0.0.1", port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
