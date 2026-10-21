"""认证模块测试（全部 mock 网络，不发真实请求）。"""

from typing import Any

import pytest

from src import auth
from src.auth import AuthError, login, parse_cookies
from src.config import Settings
from src.http_client import HttpResponse


# 全模块禁用重试的真实 sleep，避免测试变慢
@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    from src import retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda _: None)


class FakeResponse:
    """测试用响应桩。"""

    def __init__(self, status_code: int, json_data: Any = None) -> None:
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = str(self._json)

    def json(self, **kwargs: Any) -> Any:
        return self._json


class FakeClient:
    """脚本化响应序列的 HttpClient 桩。

    脚本按调用顺序出队（url 前缀匹配可选）；抛异常的项用 Exception 实例表示。
    """

    def __init__(self, script: list[tuple[str, Any]]) -> None:
        self._script = list(script)
        self.requests: list[str] = []
        self._session = None  # 显式无 jar，http_cookies 容错返回空 dict
        self.injected_cookies: dict[str, str] = {}  # 记录 set_cookies 注入

    def set_cookies(self, cookies: dict[str, str]) -> None:
        self.injected_cookies.update(cookies)

    def _next(self, url: str) -> Any:
        if not self._script:
            raise AssertionError(f"意外的请求：{url}")
        match, result = self._script.pop(0)
        if match and match not in url:
            raise AssertionError(f"期望请求 {match}，实际 {url}")
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self._next(url)  # type: ignore[return-value]

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return self._next(url)  # type: ignore[return-value]

    def close(self) -> None:
        pass


def _cookie_settings(**extra: str) -> Settings:
    return Settings(  # type: ignore[arg-type]
        **{"LINUXDO_COOKIES": "_t=abc; theme_ids=1; badfragment", **extra, "_env_file": None}
    )


# ---------- parse_cookies ----------


def test_parse_cookies_normal() -> None:
    assert parse_cookies("_t=abc; theme_ids=1") == {"_t": "abc", "theme_ids": "1"}


def test_parse_cookies_dirty() -> None:
    """脏数据：空段、无 = 段、空名称段均跳过。"""
    assert parse_cookies("  ; novalue ; =x; _t=abc ; ") == {"_t": "abc"}


def test_mask_cookie() -> None:
    assert auth.mask_cookie("_t=abcdef-ghij-klmn") == "_t=abc***lmn"
    assert auth.mask_cookie("short") == "***"


# ---------- 登录链 ----------


def test_login_via_cookies_success() -> None:
    http = FakeClient([("", FakeResponse(200, {"current_user": {"id": 1, "username": "u1"}}))])
    result = login(_cookie_settings(), http)  # type: ignore[arg-type]
    assert result.method == "cookie"
    assert result.username == "u1"


def test_cookies_injected_before_verify() -> None:
    """回归：Cookie 必须在验证请求前注入客户端（否则裸请求 404）。"""
    http = FakeClient([("", FakeResponse(200, {"current_user": {"id": 1, "username": "u1"}}))])
    login(_cookie_settings(), http)  # type: ignore[arg-type]
    assert http.injected_cookies.get("_t") == "abc"
    assert http.injected_cookies.get("theme_ids") == "1"


def test_cookie_failure_falls_back_to_password() -> None:
    """边界条件 1：Cookie 验证失败 → 降级账号密码。Cookie 链重试 3 次需 3 个 403 响应。"""
    http = FakeClient([
        ("current.json", FakeResponse(403)),  # Cookie 验证失败（重试 1）
        ("current.json", FakeResponse(403)),  # 重试 2
        ("current.json", FakeResponse(403)),  # 重试 3，放弃 Cookie 方式
        ("session/csrf", FakeResponse(200, {"csrf": "tok"})),  # CSRF
        ("/session", FakeResponse(200, {"user": {"username": "u1"}})),  # 登录
        ("current.json", FakeResponse(200, {"current_user": {"id": 1, "username": "u1"}})),  # 验证
    ])
    settings = Settings(
        LINUXDO_COOKIES="_t=stale",
        LINUXDO_USERNAME="user",
        LINUXDO_PASSWORD="pass",
        _env_file=None,
    )  # type: ignore[arg-type]
    result = login(settings, http)  # type: ignore[arg-type]
    assert result.method == "password"
    assert result.username == "u1"


def test_cookie_failure_without_credentials_raises() -> None:
    http = FakeClient([("current.json", FakeResponse(403))])
    with pytest.raises(AuthError, match="未提供账号密码"):
        login(_cookie_settings(), http)  # type: ignore[arg-type]


def test_password_login_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-2：无 Cookie 时走 CSRF → POST /session → 验证。"""
    # 绕过 with_retry 的真实 sleep（成功路径无重试，直接缩短延迟区间即可）
    http = FakeClient([
        ("session/csrf", FakeResponse(200, {"csrf": "tok"})),
        ("/session", FakeResponse(200, {"user": {"username": "u1"}})),
        ("current.json", FakeResponse(200, {"current_user": {"id": 7, "username": "u1"}})),
    ])
    settings = Settings(LINUXDO_USERNAME="u1", LINUXDO_PASSWORD="pw", _env_file=None)  # type: ignore[arg-type]
    result = login(settings, http)  # type: ignore[arg-type]
    assert result.method == "password"
    assert result.username == "u1"


def test_password_login_bad_credentials_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """凭据错误（403）不应触发重试 —— AuthError 未列入 with_retry 默认 exceptions。"""
    calls = {"session": 0}

    class CountingClient(FakeClient):
        def post(self, url: str, **kwargs: Any) -> HttpResponse:
            if "/session" in url and "csrf" not in url:
                calls["session"] += 1
                return FakeResponse(403, {"error": " incorrect username or password"})  # type: ignore[return-value]
            return super()._next(url)  # type: ignore[return-value]

    http = CountingClient([("session/csrf", FakeResponse(200, {"csrf": "tok"}))])
    settings = Settings(LINUXDO_USERNAME="u1", LINUXDO_PASSWORD="wrong", _env_file=None)  # type: ignore[arg-type]
    with pytest.raises(AuthError, match="403"):
        login(settings, http)  # type: ignore[arg-type]
    assert calls["session"] == 1


def test_verify_login_rejects_missing_user() -> None:
    http = FakeClient([("", FakeResponse(200, {"current_user": None}))])
    with pytest.raises(AuthError, match="无有效 current_user"):
        auth.verify_login(http)  # type: ignore[arg-type]
