# 部署

一期用**长轮询 + systemd**：不需要公网 IP、域名或 TLS 证书。

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
