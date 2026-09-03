# Deployment

4Seas Bot uses Telegram long polling. It does not need a public IP address, domain, or TLS certificate. Use launchd for local macOS operation or systemd for an always-on Linux host.

## macOS with launchd

```bash
cp deploy/com.4seas.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.4seas.bot.plist
launchctl start com.4seas.bot

launchctl list | grep 4seas
tail -f data/bot.log
```

Restart after changing `.env`:

```bash
launchctl kickstart -k gui/$(id -u)/com.4seas.bot
```

### Laptop limitations

1. The bot does not run while the laptop is asleep. If the machine is asleep at 19:00, that digest is missed and is not sent automatically after wake-up. Keep the machine awake or use an always-on host.
2. Only one process may poll with a Telegram bot token. Stop the launchd service before running `python -m bot` manually, or the two processes will produce Telegram `409 Conflict` errors.

## Linux with systemd

The following is a clean-host example. A managed 4Seas host may use a release directory and shared-data layout instead; follow that host's runbook when one exists.

```bash
sudo useradd -r -s /usr/sbin/nologin 4seas
sudo git clone https://github.com/4seas-community/4Seas-bot /opt/4Seas-bot
cd /opt/4Seas-bot

sudo -u 4seas python3.11 -m venv .venv
sudo -u 4seas .venv/bin/pip install -e .

sudo cp .env.example .env
sudo chmod 600 .env
sudo chown 4seas .env
sudo -e /opt/4Seas-bot/.env

sudo mkdir -p data
sudo chown 4seas data

sudo cp deploy/4seas-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now 4seas-bot
```

Check the service and follow logs:

```bash
systemctl status 4seas-bot
journalctl -u 4seas-bot -f
```

Restart after changing credentials or other `.env` settings:

```bash
sudo systemctl restart 4seas-bot
```

## Access the admin console from macOS

The admin console listens on the server's `127.0.0.1:8477` by default and must not be exposed directly to the public internet.

If the server allows SSH TCP forwarding, use a normal tunnel:

```bash
ssh -N -L 8477:127.0.0.1:8477 <user>@<host>
```

Open `http://127.0.0.1:8477/` while the tunnel is running.

If server policy disables TCP forwarding, use the repository's application-level relay. It sends the connection through a normal SSH session and still exposes the console only on your own machine:

```bash
brew install socat
BOT_ADMIN_SSH_HOST=<host> \
BOT_ADMIN_SSH_USER=<user> \
deploy/open-bot-admin.sh
```

Keep the terminal open and press `Ctrl-C` to close the relay. Set `BOT_ADMIN_LOCAL_PORT=18477` if local port 8477 is already in use.

## Release checks

Before restarting a production service:

1. Confirm the intended commit and review `git diff` or the pull request.
2. Run `uv sync --extra dev --locked` and `uv run pytest -q` in a clean checkout.
3. Back up mutable runtime data and configuration according to the host runbook.
4. Deploy without modifying the shared `.env`, SQLite database, or runtime configuration.
5. Restart the service and verify `systemctl is-active`, recent logs, the admin status panel, and `/status` in Telegram.
6. If verification fails, restore the previous release according to the host runbook, restart, and verify again.

Do not improvise a production path or service name from this generic guide. Resolve them from the target host before making a change.

## Important notes

- Run exactly one polling instance per Telegram bot token.
- The `data/` directory must be writable because it contains SQLite and WAL files.
- With `ProtectSystem=strict`, only paths listed in `ReadWritePaths` are writable.
- Event imports are idempotent; restarting or repeating a sync does not create duplicate events.
- Keep credentials and production identifiers out of Git and screenshots.
