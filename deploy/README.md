# 部署

长轮询，不需要公网 IP、域名或 TLS 证书。两种方式：Mac 上用 launchd，服务器上用 systemd。

---

## macOS（跑在自己电脑上）

```bash
cp deploy/com.4seas.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.4seas.bot.plist
launchctl start com.4seas.bot

launchctl list | grep 4seas          # 状态
tail -f data/bot.log                 # 日志
```

改完 `.env` 后重启：

```bash
launchctl kickstart -k gui/$(id -u)/com.4seas.bot
```

### ⚠️ 跑在笔记本上的两个真实限制

1. **合盖睡眠时 bot 不工作。** LaunchAgent 只在你登录状态下运行，机器睡了就挂起。
   **19:00 那台笔记本如果是睡着的，当晚的预告就不会发** —— 而且醒来也不会补发，
   因为 `run_daily` 错过就是错过了。要么保证那个时间点机器醒着
   （`caffeinate -s` 或系统设置里关掉睡眠），要么把它挪到一台常开的机器上。
2. **同一个 token 只能有一个实例。** 手动 `python -m bot` 调试前，
   先 `launchctl stop com.4seas.bot`，否则两个进程会互相抢 update（409 冲突）。

---

## Linux（systemd）

```bash
sudo useradd -r -s /usr/sbin/nologin 4seas
sudo git clone https://github.com/4seas-community/4Seas-bot /opt/4Seas-bot
cd /opt/4Seas-bot

sudo -u 4seas python3.11 -m venv .venv
sudo -u 4seas .venv/bin/pip install -e .

sudo cp .env.example .env && sudo chmod 600 .env && sudo chown 4seas .env
sudo -e /opt/4Seas-bot/.env          # 填 token 等

sudo mkdir -p data && sudo chown 4seas data

sudo cp deploy/4seas-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now 4seas-bot
```

查看：

```bash
systemctl status 4seas-bot
journalctl -u 4seas-bot -f
```

## 从 macOS 访问服务器管理页

管理页默认只监听服务器的 `127.0.0.1:8477`，不应直接暴露到公网。服务器允许 SSH TCP forwarding 时可使用普通隧道：

```bash
ssh -N -L 8477:127.0.0.1:8477 user@host
```

如果服务器策略禁止端口转发，macOS 可使用仓库自带的应用层中继脚本。它通过普通 SSH 会话连接服务器上的 `nc`，仍只在本机开放管理页：

```bash
brew install socat
BOT_ADMIN_SSH_HOST=149.28.158.244 \
BOT_ADMIN_SSH_USER=jason \
deploy/open-bot-admin.sh
```

脚本会打开 `http://127.0.0.1:8477/`。保持终端窗口开启，按 `Ctrl-C` 关闭中继。本机端口冲突时可设置 `BOT_ADMIN_LOCAL_PORT=18477`。

## 注意

- **只跑一个实例。** Telegram 的长轮询同一个 token 只允许一个消费者，跑两份会互相抢 update。
- `data/` 必须可写（SQLite + WAL 文件）。`ProtectSystem=strict` 下只有 `ReadWritePaths` 里的路径能写。
- 换 token 或改 `.env` 后 `systemctl restart 4seas-bot`。
- 导入是幂等的，重启多少次都不会产生重复活动数据。
