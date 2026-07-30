#!/usr/bin/env python3
"""列出 bot 当前能看到的所有会话及其 chat_id。

用途：新建一个群、把 bot 拉进去之后，用它拿到群 id 填进 TELEGRAM_ALLOWED_CHATS。

    python scripts/chat_ids.py            # 读一次
    python scripts/chat_ids.py --watch    # 蹲守，等新会话出现

注意：
  - 群里得先有人说过话（随便发个 /start），bot 才会收到 update。
  - 不消费 offset，所以跑完不影响 bot 正式启动时收这些消息。
  - bot 正在运行时别跑这个 —— 长轮询同一个 token 只允许一个消费者，会互相抢。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

CHAT_BEARING_KEYS = (
    "message", "edited_message", "channel_post", "edited_channel_post",
    "my_chat_member", "chat_member", "message_reaction", "callback_query",
)


def load_token() -> str:
    """依次尝试：环境变量 → 项目 .env → ~/Dev/.env"""
    if token := os.environ.get("TELEGRAM_BOT_TOKEN"):
        return token

    for path, key in (
        (Path(".env"), "TELEGRAM_BOT_TOKEN"),
        (Path.home() / "Dev" / ".env", "4SEA_TELE_BOT_TOKEN"),
    ):
        if not path.exists():
            continue
        m = re.search(rf"^{re.escape(key)}=(.*)$", path.read_text(encoding="utf-8"), re.M)
        if m:
            value = m.group(1).strip().strip("\"'")
            if value:
                print(f"# token 来自 {path} 的 {key}", file=sys.stderr)
                return value

    sys.exit("找不到 token。设置 TELEGRAM_BOT_TOKEN 环境变量，或在 .env 里配好。")


def api(token: str, method: str, params: str = "") -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def collect_chats(token: str) -> dict[int, tuple[str, str]]:
    body = api(token, "getUpdates", "?limit=100&timeout=0")
    if not body.get("ok"):
        sys.exit(f"getUpdates 失败：{body}")

    found: dict[int, tuple[str, str]] = {}
    for update in body.get("result", []):
        for key in CHAT_BEARING_KEYS:
            obj = update.get(key)
            chat = (obj or {}).get("chat") or (obj or {}).get("message", {}).get("chat")
            if chat:
                title = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
                found[chat["id"]] = (chat.get("type", "?"), title)
    return found


def show(chats: dict[int, tuple[str, str]]) -> None:
    if not chats:
        print("没看到任何会话。在目标群里发一条消息（比如 /start）再试。")
        return
    print(f"\n{'chat_id':>16}  {'type':<12} title")
    print("-" * 60)
    for cid, (ctype, title) in sorted(chats.items()):
        print(f"{cid:>16}  {ctype:<12} {title}")

    groups = [cid for cid, (t, _) in chats.items() if t != "private"]
    if groups:
        print(f"\n填进 .env：\nTELEGRAM_ALLOWED_CHATS={','.join(str(c) for c in groups)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="蹲守，等新会话出现")
    parser.add_argument("--interval", type=int, default=3, help="蹲守轮询间隔秒数")
    parser.add_argument("--timeout", type=int, default=300, help="蹲守最长等待秒数")
    args = parser.parse_args()

    token = load_token()
    me = api(token, "getMe").get("result", {})
    print(f"Bot: @{me.get('username')} (id {me.get('id')})")
    print(f"privacy mode 已关闭: {me.get('can_read_all_group_messages')}")

    seen = collect_chats(token)
    show(seen)

    if not args.watch:
        return

    print(f"\n蹲守中（最多 {args.timeout}s）—— 现在去建群、把 bot 拉进去、发一条消息…")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(args.interval)
        current = collect_chats(token)
        new = {cid: v for cid, v in current.items() if cid not in seen}
        if new:
            print("\n🎉 发现新会话：")
            show(new)
            return
        seen = current
    print("超时，没等到新会话。确认 bot 已加进群、且群里有人发过消息。")


if __name__ == "__main__":
    main()
