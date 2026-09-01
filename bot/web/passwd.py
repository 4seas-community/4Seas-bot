"""`python -m bot.web.passwd` —— 生成/更换管理页密码。

默认直接改写本机 `.env` 里的 `WEB_PASSWORD_HASH`（`.env` 已 gitignore），
省掉「复制一长串再手动粘进去」这一步 —— 那一步最容易粘漏一个字符，然后
对着"密码不对"排查半小时。

    python -m bot.web.passwd              # 随机生成一个强密码并写入 .env
    python -m bot.web.passwd --ask        # 自己输密码（不回显）
    python -m bot.web.passwd --print-only # 只打印摘要，不碰 .env

改完要重启 bot 才生效（摘要是启动时读的）。改密码会把所有已登录的会话踢下线。
"""

from __future__ import annotations

import argparse
import getpass
import re
import secrets
import sys
from pathlib import Path

from .auth import hash_password

KEY = "WEB_PASSWORD_HASH"
# 去掉了容易看错的 0/O/1/l/I。~62 bit 熵，够抗离线爆破了。
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def upsert_env(path: Path, key: str, value: str) -> str:
    """就地改写一行，保留其余内容和注释。返回做了什么。

    不用 dotenv 的 set_key：它会重排/加引号，把手写的注释布局打乱。
    """
    line = f"{key}={value}"
    if not path.exists():
        path.write_text(line + "\n", encoding="utf-8")
        return f"created {path}"

    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        path.write_text(pattern.sub(lambda _: line, text, count=1), encoding="utf-8")
        return f"updated {key} in {path}"
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n# 管理页登录密码（scrypt 摘要，用 python -m bot.web.passwd 生成）\n" + line + "\n",
                    encoding="utf-8")
    return f"added {key} to {path}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bot.web.passwd", description=__doc__)
    ap.add_argument("--ask", action="store_true", help="交互输入密码，而不是随机生成")
    ap.add_argument("--print-only", action="store_true", help="只打印，不写 .env")
    ap.add_argument("--env-file", default=".env", help="要改写的 env 文件（默认 .env）")
    args = ap.parse_args(argv)

    if args.ask:
        password = getpass.getpass("新密码: ")
        if not password:
            print("密码不能为空", file=sys.stderr)
            return 1
        if password != getpass.getpass("再输一次: "):
            print("两次输入不一致", file=sys.stderr)
            return 1
        if len(password) < 8:
            print("⚠️  少于 8 位。这个页面能改 bot 在群里说什么，建议长一点。", file=sys.stderr)
    else:
        password = generate_password()

    digest = hash_password(password)

    if args.print_only:
        print(f"{KEY}={digest}")
    else:
        print(upsert_env(Path(args.env_file), KEY, digest))

    if not args.ask:
        print()
        print(f"  密码: {password}")
        print("  ↑ 只显示这一次，存进密码管理器。服务器上只有它的 scrypt 摘要。")
    print()
    print("重启 bot 后生效：./start.sh --stop && ./start.sh --bg")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
