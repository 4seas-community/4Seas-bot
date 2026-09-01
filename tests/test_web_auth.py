"""管理页登录。

这一层守的是「谁能改 bot 在 776 人群里说什么」，所以测试重点不是 happy path，
而是每一条本来可以绕过去的路：改 cookie 里的过期时间、换个 Host 头、无限试密码。
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.web import auth
from bot.web import server as web_server


# ── 哈希 ──────────────────────────────────────────────────────────────


def test_hash_roundtrip():
    digest = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", digest)
    assert not auth.verify_password("correct horse battery stapl", digest)


def test_hash_is_salted_per_call():
    """同一个密码两次生成的摘要必须不同，否则 .env 泄露就能看出谁和谁用了同一个密码。"""
    assert auth.hash_password("hunter2") != auth.hash_password("hunter2")


def test_empty_password_never_verifies():
    digest = auth.hash_password("hunter2")
    assert not auth.verify_password("", digest)
    with pytest.raises(ValueError):
        auth.hash_password("")


@pytest.mark.parametrize("broken", ["", "nonsense", "scrypt$x$8$1$aa$bb", "bcrypt$1$2$3$aa$bb",
                                    "scrypt$32768$8$1$notbase64!!$bb"])
def test_broken_digest_fails_closed(broken):
    """.env 抄坏了要拒绝登录，不能抛异常（500 会把登录页卡死），更不能放行。"""
    assert auth.verify_password("anything", broken) is False


# ── 会话签名 ──────────────────────────────────────────────────────────


def test_session_roundtrip():
    secret, digest = b"s" * 32, auth.hash_password("pw")
    token = auth.issue_session(secret, digest, 3600)
    assert auth.check_session(token, secret, digest)


def test_expired_session_rejected():
    secret, digest = b"s" * 32, auth.hash_password("pw")
    assert not auth.check_session(auth.issue_session(secret, digest, -1), secret, digest)


def test_forged_expiry_rejected():
    """把过期时间往后改一位就等于永久会话 —— 签名必须覆盖 payload。"""
    secret, digest = b"s" * 32, auth.hash_password("pw")
    token = auth.issue_session(secret, digest, 3600)
    payload, _, sig = token.partition(".")
    assert not auth.check_session(f"{int(payload) + 999999}.{sig}", secret, digest)


def test_password_change_invalidates_sessions():
    secret = b"s" * 32
    old = auth.hash_password("old")
    token = auth.issue_session(secret, old, 3600)
    assert not auth.check_session(token, secret, auth.hash_password("new"))


def test_other_secret_rejected():
    digest = auth.hash_password("pw")
    token = auth.issue_session(b"a" * 32, digest, 3600)
    assert not auth.check_session(token, b"b" * 32, digest)


@pytest.mark.parametrize("junk", ["", ".", "abc", "abc.", ".abc", "abc.!!!"])
def test_malformed_cookie_rejected(junk):
    assert auth.check_session(junk, b"s" * 32, auth.hash_password("pw")) is False


def test_secret_is_persisted_and_private(tmp_path):
    path = tmp_path / "nested" / "session_secret"
    first = auth.load_or_create_secret(path)
    assert len(first) >= 32
    # 重启后必须读回同一个，否则每次重启都把人踢下线。
    assert auth.load_or_create_secret(path) == first
    assert (path.stat().st_mode & 0o077) == 0


# ── 限流 ──────────────────────────────────────────────────────────────


def test_throttle_blocks_after_limit():
    th = auth.LoginThrottle(limit=3, window=100)
    for i in range(3):
        assert th.retry_after("1.1.1.1", now=i) == 0
        th.record_failure("1.1.1.1", now=i)
    assert th.retry_after("1.1.1.1", now=3) > 0
    # 别的来源不受影响
    assert th.retry_after("2.2.2.2", now=3) == 0


def test_throttle_window_slides():
    th = auth.LoginThrottle(limit=2, window=100)
    th.record_failure("x", now=0)
    th.record_failure("x", now=1)
    assert th.retry_after("x", now=50) > 0
    assert th.retry_after("x", now=200) == 0


def test_success_clears_failures():
    th = auth.LoginThrottle(limit=2, window=100)
    th.record_failure("x", now=0)
    th.record_failure("x", now=1)
    th.reset("x")
    assert th.retry_after("x", now=2) == 0


def test_throttle_does_not_grow_without_bound():
    """查一次就建一个桶的话，公网上随便打几百万个 IP 就是一条内存耗尽通道。"""
    th = auth.LoginThrottle(limit=2, window=100)
    for i in range(50):
        th.retry_after(f"ip-{i}", now=0)
    assert th._fails == {}
    th.record_failure("ip-0", now=0)
    assert th.retry_after("ip-0", now=500) == 0
    assert th._fails == {}  # 过期的桶自己清掉


# ── HTTP ──────────────────────────────────────────────────────────────

PASSWORD = "s3cret-passphrase"


class _FakeManager:
    app = None


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "SECRET_FILE", tmp_path / "session_secret")
    monkeypatch.setattr(web_server.settings, "web_password_hash", auth.hash_password(PASSWORD))
    monkeypatch.setattr(web_server.settings, "web_token", "test-token")
    monkeypatch.setattr(web_server.settings, "commands_dir", str(tmp_path / "commands"))
    return web_server.AdminServer(_FakeManager())


@pytest.fixture
async def client(admin):
    async with TestClient(TestServer(admin._build())) as c:
        yield c


async def test_api_requires_auth(client):
    assert (await client.get("/api/status")).status == 401


async def test_root_serves_login_page_when_locked(client):
    body = await (await client.get("/")).text()
    assert "/api/login" in body and "id=\"pw\"" in body


async def test_login_then_api_works(client):
    res = await client.post("/api/login", json={"password": PASSWORD})
    assert res.status == 200
    raw = res.headers["Set-Cookie"]
    assert raw.startswith(f"{auth.COOKIE_NAME}=")
    # HttpOnly：页面上的脚本读不到它，XSS 也偷不走。
    # SameSite=Strict：别的站点发起的请求不会带上它，CSRF 就不成立。
    assert "httponly" in raw.lower()
    assert "samesite=strict" in raw.lower()
    assert (await client.get("/api/whoami")).status == 200
    assert "id=\"pw\"" not in await (await client.get("/")).text()


async def test_wrong_password_rejected(client):
    assert (await client.post("/api/login", json={"password": "nope"})).status == 401
    assert (await client.get("/api/status")).status == 401


async def test_login_is_throttled(client, admin):
    admin.throttle = auth.LoginThrottle(limit=2, window=300)
    for _ in range(2):
        assert (await client.post("/api/login", json={"password": "nope"})).status == 401
    res = await client.post("/api/login", json={"password": "nope"})
    assert res.status == 429
    # 锁定期内即使密码正确也不放行 —— 否则限流可以被"猜对一次"绕过去。
    assert (await client.post("/api/login", json={"password": PASSWORD})).status == 429


async def test_login_survives_garbage_body(client):
    res = await client.post("/api/login", data="not json",
                            headers={"Content-Type": "application/json"})
    assert res.status == 401


async def test_token_still_works_for_scripts(client):
    """密码是给人用的，token 是给 curl / 脚本用的，两条路都要通。"""
    assert (await client.get("/api/whoami", headers={"X-Admin-Token": "test-token"})).status == 200


async def test_logout_clears_session(client):
    await client.post("/api/login", json={"password": PASSWORD})
    assert (await client.post("/api/logout")).status == 200
    assert (await client.get("/api/status")).status == 401


async def test_cookie_ignored_on_unknown_host(client):
    """DNS rebinding：恶意域名可以解析到 127.0.0.1，Host 头是唯一能识破的东西。

    浏览器会自动带上 cookie，所以只有 cookie 这条路需要查 Host。
    """
    await client.post("/api/login", json={"password": PASSWORD})
    assert (await client.get("/api/status")).status == 200
    res = await client.get("/api/status", headers={"Host": "evil.example.com"})
    assert res.status == 401


async def test_login_refuses_unknown_host_with_a_reason(client):
    """在这里发 cookie 只会得到一个登不进去的登录页，不如直接说清楚。"""
    res = await client.post("/api/login", json={"password": PASSWORD},
                            headers={"Host": "evil.example.com"})
    assert res.status == 400
    assert (await res.json())["reason"] == "bad_host"


async def test_token_unaffected_by_host(client):
    """token 不会被浏览器自动带上，不存在 rebinding 问题。
    WEB_HOST=0.0.0.0 + 局域网 IP 访问的老用法不能被这次改动搞坏。"""
    res = await client.get("/api/whoami", headers={"Host": "192.168.1.5:8477",
                                                   "X-Admin-Token": "test-token"})
    assert res.status == 200


async def test_configured_host_allowed(client, monkeypatch):
    monkeypatch.setattr(web_server.settings, "web_allowed_hosts", "bot.example.com")
    res = await client.post("/api/login", json={"password": PASSWORD},
                            headers={"Host": "bot.example.com"})
    assert res.status == 200


async def test_no_password_configured_falls_back_to_token(tmp_path, monkeypatch):
    """没设密码时行为和以前一样：token 认证，`/` 直接给管理页。"""
    monkeypatch.setattr(web_server, "SECRET_FILE", tmp_path / "session_secret")
    monkeypatch.setattr(web_server.settings, "web_password_hash", "")
    monkeypatch.setattr(web_server.settings, "web_token", "test-token")
    server = web_server.AdminServer(_FakeManager())
    async with TestClient(TestServer(server._build())) as c:
        assert "id=\"pw\"" not in await (await c.get("/")).text()
        assert (await c.get("/api/status")).status == 401
        assert (await c.post("/api/login", json={"password": "anything"})).status == 503
