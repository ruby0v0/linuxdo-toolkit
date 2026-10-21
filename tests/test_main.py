"""main 编排测试：mock auth/browse/notify，验证退出码规则与 dry-run（BR-4）。"""

from typing import Any

import pytest

from src import main as main_mod
from src.auth import AuthResult
from src.browse import BrowseStats
from src.config import Settings
from src.models import RunResult


def _settings(**extra: str) -> Settings:
    return Settings(**{"LINUXDO_COOKIES": "_t=x", **extra, "_env_file": None})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _patch_heavy(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换全部副作用：登录恒成功、无真实浏览器、通知为空操作。"""
    monkeypatch.setattr(
        main_mod.auth,
        "login",
        lambda settings, http: AuthResult(method="cookie", username="u", cookies={"_t": "x"}),
    )
    monkeypatch.setattr(main_mod, "create_client", lambda: _FakeHttpClient())
    monkeypatch.setattr(main_mod.notify, "notify", lambda settings, result: None)


class _FakeHttpClient:
    def get(self, url: str, **kw: Any) -> None: ...
    def post(self, url: str, **kw: Any) -> None: ...
    def close(self) -> None: ...


def test_run_success_with_browse(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_browse(page: Any, settings: Settings) -> BrowseStats:
        return BrowseStats(total=3, visited=3, topics=["a", "b", "c"])

    monkeypatch.setattr(main_mod.browse, "browse", fake_browse)
    monkeypatch.setattr(main_mod, "BrowserSession", lambda settings: _FakeSession())
    result = main_mod.run(_settings(BROWSE_COUNT="3"))
    assert result.success is True
    assert result.browse_visited == 3
    assert result.auth_method == "cookie"


class _FakeSession:
    def start(self, cookies: dict[str, str]) -> None:
        return None

    def close(self) -> None:
        pass


def test_run_success_browse_disabled() -> None:
    """AC-4：BROWSE_ENABLED=false 只登录验证，成功。"""
    result = main_mod.run(_settings(BROWSE_ENABLED="false"))
    assert result.success is True
    assert result.browse_enabled is False


def test_run_zero_topics_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """边界条件 2：登录成功但 0 帖 → 失败（退出码 1 的来源）。"""
    monkeypatch.setattr(
        main_mod.browse, "browse", lambda page, s: BrowseStats(total=6, visited=0)
    )
    monkeypatch.setattr(main_mod, "BrowserSession", lambda settings: _FakeSession())
    result = main_mod.run(_settings())
    assert result.success is False
    assert result.error is not None and "0 帖" in result.error


def test_run_auth_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.auth import AuthError

    def fail_login(settings: Settings, http: Any) -> AuthResult:
        raise AuthError("凭据无效")

    monkeypatch.setattr(main_mod.auth, "login", fail_login)
    result = main_mod.run(_settings())
    assert result.success is False
    assert "凭据无效" in (result.error or "")


def test_run_dry_run_skips_browse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q3：dry-run 只验证登录，不进浏览器。"""
    called = {"browse": False}

    def fake_browse(page: Any, settings: Settings) -> BrowseStats:
        called["browse"] = True
        return BrowseStats()

    monkeypatch.setattr(main_mod.browse, "browse", fake_browse)
    monkeypatch.setattr(main_mod, "BrowserSession", lambda settings: (_ for _ in ()).throw(
        AssertionError("dry-run 不应启动浏览器")
    ))
    result = main_mod.run(_settings(), dry_run=True)
    assert result.success is True
    assert called["browse"] is False


def test_run_result_summary_formats() -> None:
    ok = RunResult(success=True, auth_method="password", username="u",
                   browse_enabled=True, browse_visited=6, browse_total=6,
                   likes_given=1, duration_seconds=187.2)
    assert "浏览=6/6" in ok.summary
    assert "password" in ok.summary
    bad = RunResult(success=False, error="凭据无效", duration_seconds=3.0)
    assert "凭据无效" in bad.summary
