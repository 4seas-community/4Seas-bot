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

## 注意

- **只跑一个实例。** Telegram 的长轮询同一个 token 只允许一个消费者，跑两份会互相抢 update。
- `data/` 必须可写（SQLite + WAL 文件）。`ProtectSystem=strict` 下只有 `ReadWritePaths` 里的路径能写。
- 换 token 或改 `.env` 后 `systemctl restart 4seas-bot`。
- 导入是幂等的，重启多少次都不会产生重复活动数据。
