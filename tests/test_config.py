"""配置模块测试。"""

import pytest

from src.config import Settings


def _base_env(**extra: str) -> dict[str, str]:
    env: dict[str, str] = {"LINUXDO_COOKIES": "_t=abc; theme_ids=1"}
    env.update(extra)
    return env


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINUXDO_COOKIES", "_t=abc")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.browse_enabled is True
    assert s.browse_count == 6
    assert s.like_enabled is False
    assert s.notify_enabled is False
    assert s.headless is True


def test_missing_credentials_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINUXDO_COOKIES", raising=False)
    monkeypatch.delenv("LINUXDO_USERNAME", raising=False)
    monkeypatch.delenv("LINUXDO_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="缺少登录凭据"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_username_without_password_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINUXDO_COOKIES", raising=False)
    monkeypatch.setenv("LINUXDO_USERNAME", "user")
    monkeypatch.delenv("LINUXDO_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="缺少登录凭据"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_browse_count_out_of_range_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-5: BROWSE_COUNT 越界（>20）时 fail-fast。"""
    for env, kwargs in (
        (_base_env(BROWSE_COUNT="25"), {"browse_count": 25}),
        (_base_env(BROWSE_COUNT="0"), {"browse_count": 0}),
    ):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        with pytest.raises(ValueError):
            Settings(_env_file=None)  # type: ignore[call-arg]
        del kwargs


def test_notify_enabled_requires_telegram_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env(NOTIFY_ENABLED="true").items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="NOTIFY_TELEGRAM_BOT_TOKEN"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_notify_disabled_without_config_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.notify_enabled is False


def test_blank_cookies_fall_back_to_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """空白 Cookie 视为未提供，has_cookies=False 且凭据校验通过。"""
    for key, value in _base_env(
        LINUXDO_COOKIES="  ", LINUXDO_USERNAME="user", LINUXDO_PASSWORD="pass"
    ).items():
        monkeypatch.setenv(key, value)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.has_cookies is False
    assert s.has_credentials is True
