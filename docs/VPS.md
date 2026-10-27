# Развёртывание на Ubuntu VPS (порт 8020)

Это **отдельная VPS-конфигурация**, не WSL-лаунчер. Она не открывает
порт приложения в локальную сеть: SSH-туннель или отдельный корпоративный
HTTPS reverse proxy обязателен. Не делайте `0.0.0.0:8020` публичным:
даже при личных паролях требуется защищённый канал.

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

## Обновление аутентификации

На первом запуске новой версии приложение мигрирует базу на схему 2 и один раз отзовёт старые сессии. Предварительно создайте резервную копию. Исходный администратор входит со старым административным паролем; сотрудникам администратор выдаёт одноразовый код через «Администрирование → Сотрудники → Сбросить пароль», чтобы они самостоятельно установили личный пароль. Больше нет входа с пустым паролем. Сессии продлеваются при активности до 14 дней после последнего продления. При применении reverse proxy задайте внешний HTTPS URL в `NK_PUBLIC_URL`.

## Windows-подписант через SSH-туннель и отдельный публичный адрес

Если `NK_PUBLIC_URL` задан как корпоративный адрес (например,
`http://10.0.0.171:8020` или `https://nk.example.internal`), адрес
`127.0.0.1` из SSH-туннеля по умолчанию отклоняется проверкой Host.

Для подписанта включите **отдельное, необязательное** разрешение на VPS:
добавьте в `/etc/nk-manager.env` строку (только один раз):

```ini
NK_SIGNER_ALLOW_LOOPBACK_HOST=1
```

После сохранения перезапустите службу `sudo systemctl restart nk-manager`.
Разрешение действует **только** для `/api/signer/*` с Host `localhost`
или `127.0.0.1`. Остальной веб-интерфейс по-прежнему принимает только
разрешённый Host. Подписант обязан предъявить отдельный Bearer-токен,
который не является API KEY Национального каталога.

На Windows откройте PowerShell и создайте туннель:

```powershell
ssh -o ExitOnForwardFailure=yes -N -L 127.0.0.1:18020:127.0.0.1:8020 USER@VPS
```

Замените `USER@VPS` на свои SSH-данные. Указанный удалённый адрес
`127.0.0.1:8020` соответствует рекомендуемой конфигурации, где
приложение слушает только loopback **на самом VPS**. Если сервер работает
за другим локальным reverse proxy, выбирайте фактически доступный
с VPS адрес приложения. Окно туннеля должно оставаться открытым.

Проверка транспорта (без доступа к интерфейсу через loopback Host):

```powershell
curl.exe --noproxy '*' -H "Host: 10.0.0.171:8020" http://127.0.0.1:18020/health
```

Значение заголовка Host замените на хост и порт, соответствующие вашему
`NK_PUBLIC_URL`. Ответ `400` на обычный запрос к `/health` **без**
этого заголовка при корпоративном `NK_PUBLIC_URL` ожидаем: исключение
создано исключительно для подписанта.

Скопируйте `signer/Start-Signer.ps1` и `signer/Sign-Payload.vbs`
на Windows. Отпечаток сертификата можно получить командой:

```powershell
Get-ChildItem Cert:\CurrentUser\My | Where-Object HasPrivateKey | Select-Object Subject,Thumbprint,NotAfter
```

Запустите `Start-Signer.ps1 -Server http://127.0.0.1:18020 -Thumbprint
YOUR_SHA1 -Diagnose` для локальной проверки сертификата и затем с
`-CheckConnection` для сетевой проверки. При запросе введите **токен
подписанта** из настроек НК. Не помещайте токен в аргументы команды,
общие чаты или логи. HTTP для такого режима допустим только внутри
локального SSH-туннеля.
