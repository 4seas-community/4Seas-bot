"""管理页登录：一个密码，没有用户名。

为什么密码摘要放 `.env` 而不是写死在代码里 —— 这个仓库是**公开**的。写死等于
把校验值连同参数一起发布出去，任何人都可以拿回本地慢慢跑字典；而摘要放在
`.env`（已 gitignore）里，代码本身不含任何秘密，泄露面只剩服务器本身。
盐也不需要单独一个变量：scrypt 的盐每次生成时随机，直接编在摘要字符串里，
换密码就换盐，不用记两个东西。

三层：
* `hash_password` / `verify_password` —— scrypt，慢哈希，撞库成本高。
* 会话 cookie —— HMAC 签名，密钥 = 服务器私有 secret 派生。密码改了旧会话立刻失效。
* `LoginThrottle` —— 失败限流。回环地址上也要有：SSH 隧道另一头是谁并不由我们决定。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

# scrypt 参数。N=2^15 / r=8 在这台机器上约 60ms —— 交互登录感觉不到，
# 但离线爆破一个 12 位随机密码要按世纪算。
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
# hashlib.scrypt 默认 maxmem=0（内部上限 32MB），128*N*r 已经 32MB，
# 不显式放宽会直接 ValueError。
SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2

COOKIE_NAME = "4seas_admin"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ── 密码 ──────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """→ `scrypt$N$r$p$salt$hash`，整串可以直接塞进 .env。"""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """摘要格式坏掉时返回 False，不抛异常 —— 手抄 .env 抄错不该让登录接口 500。"""
    if not password or not encoded:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.strip().split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt_b64),
            n=int(n), r=int(r), p=int(p),
            maxmem=128 * int(n) * int(r) * 2, dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        log.warning("WEB_PASSWORD_HASH is not a valid scrypt digest — login will always fail")
        return False
    return hmac.compare_digest(dk, expected)


# ── 会话密钥 ──────────────────────────────────────────────────────────


def load_or_create_secret(path: Path) -> bytes:
    """会话签名用的服务器私钥，落盘保存。

    每次启动随机生成的话，重启就把所有人踢下线；而这个 bot 白天是会重启的。
    权限 0600，并且只在新建时设 —— 已经存在的文件不去改用户自己的权限。
    """
    try:
        raw = path.read_bytes().strip()
        if len(raw) >= 32:
            return raw
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("session secret unreadable (%s) — using an in-memory one", exc)
        return secrets.token_bytes(32)

    raw = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
    except OSError as exc:
        log.warning("could not persist session secret (%s) — sessions end at restart", exc)
    return raw


def _session_key(secret: bytes, password_hash: str) -> bytes:
    """把密码摘要拌进签名密钥：改密码 = 所有已发出的 cookie 当场作废。"""
    return hmac.new(secret, b"4seas-admin-session|" + password_hash.encode(), hashlib.sha256).digest()


def issue_session(secret: bytes, password_hash: str, ttl_seconds: int, *, now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + ttl_seconds)
    payload = str(exp)
    sig = hmac.new(_session_key(secret, password_hash), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def check_session(value: str, secret: bytes, password_hash: str, *, now: float | None = None) -> bool:
    if not value or not password_hash:
        return False
    payload, _, sig_b64 = value.partition(".")
    if not payload or not sig_b64:
        return False
    expected = hmac.new(_session_key(secret, password_hash), payload.encode(), hashlib.sha256).digest()
    try:
        supplied = _unb64(sig_b64)
    except (ValueError, TypeError):
        return False
    # 先验签再看过期时间：payload 没签名保护的话，改一下就是永久会话。
    if not hmac.compare_digest(supplied, expected):
        return False
    try:
        return int(payload) > (time.time() if now is None else now)
    except ValueError:
        return False


# ── 失败限流 ──────────────────────────────────────────────────────────


class LoginThrottle:
    """滑动窗口。`window` 秒内失败满 `limit` 次就拒绝，直到最早那次滑出窗口。

    按来源 IP 分桶。走 SSH 隧道时所有请求都是 127.0.0.1，也就是说自己连错
    太多次也会把自己锁住几分钟 —— 这是刻意的取舍：宁可等，不要留个能无限
    试密码的口子。
    """

    # 桶数上限。绑到公网时，每个来源 IP 一个桶，不封顶就是一条慢速内存耗尽通道。
    MAX_KEYS = 4096

    def __init__(self, limit: int = 8, window: int = 300) -> None:
        self.limit = limit
        self.window = window
        self._fails: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        """只读地取桶：查询不该凭空创建条目，否则查一次就多占一份内存。"""
        bucket = self._fails.get(key)
        if bucket is None:
            return deque()
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if not bucket:
            del self._fails[key]
        return bucket

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        """还要等几秒才能再试；0 表示现在可以试。"""
        now = time.monotonic() if now is None else now
        bucket = self._prune(key, now)
        if len(bucket) < self.limit:
            return 0
        return max(1, int(self.window - (now - bucket[0])) + 1)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        bucket = self._prune(key, now)
        if not bucket and len(self._fails) >= self.MAX_KEYS:
            # 满了就先扫一遍过期的；还是满就不再记新来源。已在册的照常限流，
            # 不因为表满而放松 —— 宁可少记一个，也不能把已有的挤掉。
            for stale in [k for k, v in self._fails.items() if v and now - v[-1] > self.window]:
                del self._fails[stale]
            if len(self._fails) >= self.MAX_KEYS:
                log.warning("login throttle table is full (%d sources)", len(self._fails))
                return
        bucket.append(now)
        self._fails[key] = bucket

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
