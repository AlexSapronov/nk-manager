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

## Подписант Windows через SSH-туннель при корпоративном IP

Если портал NK работает по `http://10.0.0.171:8020` и `NK_PUBLIC_URL` строго
совпадает с этим адресом, локальный URL туннеля (`localhost`) иначе отвергается
проверкой Host. Для **только** подписанта включите на VPS:

```bash
sudo sh -c 'printf "\\nNK_SIGNER_ALLOW_LOOPBACK_HOST=1\\n" >> /etc/nk-manager.env'
sudo systemctl restart nk-manager
```

Флаг разрешает Host `localhost` / `127.0.0.1` исключительно на
`/api/signer/*`. Веб-портал по-прежнему принимает только Host из
`NK_PUBLIC_URL`. Подписант проверяет отдельный Bearer-токен и отпечаток
сертификата; доступ к SSH разрешайте только доверенным пользователям.

На Windows в **отдельном окне PowerShell**:

```powershell
ssh -o ExitOnForwardFailure=yes -N -L 127.0.0.1:18020:10.0.0.171:8020 smartuser@10.0.0.171
```

Окно SSH оставьте открытым. `http://127.0.0.1:18020/health` может ответить
400: настройка намеренно разрешает loopback Host **только** на
`/api/signer/*`. Это не ошибка самого туннеля. Чтобы проверить транспорт,
используйте запрос с корпоративным Host:

```powershell
curl.exe --noproxy '*' -H "Host: 10.0.0.171:8020" http://127.0.0.1:18020/health
```

Получите SHA-1 сертификата:
```powershell
Get-ChildItem Cert:\\CurrentUser\\My | Where-Object HasPrivateKey | Select-Object Subject,Thumbprint,NotAfter
```

Из Ubuntu VPS скопируйте файлы `signer/Start-Signer.ps1` и
`signer/Sign-Payload.vbs` **на Windows**, например через `scp`.
Запускайте на Windows `Start-Signer.ps1 -Server http://127.0.0.1:18020
-Thumbprint SHA1 -Diagnose`, затем с `-CheckConnection`. `-Diagnose`
не требует соединения и не отправляет данные. Для `-CheckConnection`
вставьте секретный signer connection token из админки NK при скрытом запросе;
не передавайте его в командной строке или чате. Нельзя подставлять вместо
него API KEY Национального каталога.
