# Развёртывание на Ubuntu VPS (порт 8020)

Это **отдельная VPS-конфигурация**, не WSL-лаунчер. Она не открывает
порт приложения в локальную сеть: SSH-туннель или отдельный корпоративный
HTTPS reverse proxy обязателен. Не делайте `0.0.0.0:8020` публичным:
сотрудники входят без пароля.

## Первичная установка

Для Ubuntu с Python 3.12:

```bash
sudo apt-get update
sudo apt-get install -y git python3.12-venv
sudo useradd --system --home-dir /var/lib/nk-manager --shell /usr/sbin/nologin nkmanager
sudo install -d -o nkmanager -g nkmanager -m 0700 /var/lib/nk-manager
sudo git clone https://github.com/AlexSapronov/nk-manager.git /opt/nk-manager
cd /opt/nk-manager
sudo python3.12 -m venv .venv
sudo .venv/bin/python -m pip install -r requirements-lock.txt
```

Инициализация первого пользователя **до** запуска службы; пароль
вводится интерактивно, без аргументов командной строки:

```bash
sudo -u nkmanager /opt/nk-manager/.venv/bin/python /opt/nk-manager/scripts/setup.py --data /var/lib/nk-manager
```

Настройка systemd. В приведённом варианте обращение идёт через
SSH-туннель как `http://localhost:8020`:

```bash
printf 'NK_DATA=/var/lib/nk-manager\nNK_PUBLIC_URL=http://localhost:8020\n' | sudo tee /etc/nk-manager.env >/dev/null
sudo chown root:root /etc/nk-manager.env
sudo chmod 0600 /etc/nk-manager.env
sudo cp /opt/nk-manager/deploy/nk-manager.service /etc/systemd/system/nk-manager.service
sudo systemctl daemon-reload
sudo systemctl enable --now nk-manager.service
curl -fsS http://127.0.0.1:8020/health
```

Для Windows-клиента запустите в PowerShell туннель, используя ваши
SSH-логин и адрес VPS:

```powershell
ssh -N -L 8020:127.0.0.1:8020 USER@VPS
```

После этого интерфейс: `http://localhost:8020`. При переходе на
HTTPS через корпоративный reverse proxy задайте в
`/etc/nk-manager.env` точный внешний origin в `NK_PUBLIC_URL`
(например, `https://catalog.example.internal`), перезапустите
службу и проверьте Origin/Host. Не добавляйте несколько разнородных
адресов: приложение строго сверяет origin.

## Диагностика и обновления

```bash
sudo systemctl status nk-manager --no-pager -l
sudo journalctl -u nk-manager -n 100 --no-pager
sudo ss -lntp | grep ':8020'
```

Перед обновлением делайте резервную копию SQLite через
`scripts/backup.py` по `docs/OPERATIONS.md`. Внешние секреты НК
(`/var/lib/nk-manager/nk-secrets.json`) сохраняются отдельно.
Пользователь `nkmanager` владеет только папкой данных. Один worker:
фоновая очередь и SQLite рассчитаны на один процесс.

Подписание с удалённого Windows-ПК по HTTP допускается **только
через локальный SSH-туннель** (`localhost`); для прямого обращения к
VPS из Windows-скрипта используйте HTTPS и отдельный токен подписанта.
