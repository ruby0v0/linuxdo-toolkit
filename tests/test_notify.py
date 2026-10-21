"""通知模块测试（AC-7：注册表分发、异常不外抛）。"""

import pytest

from src import notify as notify_mod
from src.config import Settings
from src.models import RunResult


def _settings(**extra: str) -> Settings:
    base: dict[str, str] = {
        "LINUXDO_COOKIES": "_t=x",
        "NOTIFY_ENABLED": "true",
        "NOTIFY_CHANNEL": "telegram",
        "NOTIFY_TELEGRAM_BOT_TOKEN": "tok",
        "NOTIFY_TELEGRAM_CHAT_ID": "123",
    }
    base.update(extra)
    return Settings(**base)  # type: ignore[arg-type]


def _with_channel(s: Settings, channel: str) -> Settings:
    """绕过 StrEnum 校验注入任意渠道名（模拟注册表新渠道）。"""
    return s.model_copy(update={"notify_channel": channel})


def _result(success: bool = True) -> RunResult:
    return RunResult(success=success, auth_method="cookie", username="u")


def test_notify_dispatches_to_registered_sender() -> None:
    sent: list[tuple[str, str]] = []

    class FakeSender:
        def send(self, settings: Settings, title: str, body: str) -> None:
            sent.append((title, body))

    notify_mod.register_sender("telegram", FakeSender())  # type: ignore[arg-type]
    sent.clear()  # 注册时可能已替换真实 sender，清空记录
    notify_mod.notify(_settings(), _result())
    assert len(sent) == 1
    assert "成功" in sent[0][1]


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def send(self, settings: Settings, title: str, body: str) -> None:
        if self.fail:
            raise RuntimeError("send failed")
        self.calls.append(body)


def test_notify_swallows_send_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """BR-5：发送异常仅 warning，不外抛。"""
    notify_mod.register_sender("telegram", FakeSender(fail=True))
    notify_mod.notify(_settings(), _result())  # 不抛异常即通过


def test_notify_disabled_skips() -> None:
    fake = FakeSender()
    notify_mod.register_sender("telegram", fake)
    notify_mod.notify(_settings(NOTIFY_ENABLED="false"), _result())
    assert fake.calls == []


def test_notify_unknown_channel_skipped() -> None:
    fake = FakeSender()
    notify_mod.register_sender("telegram", fake)
    s = _with_channel(_settings(), "carrier-pigeon")
    notify_mod.notify(s, _result())
    assert fake.calls == []


def test_register_new_channel_without_core_changes() -> None:
    """AC-7：新渠道 = register_sender 一行，core 不改。"""
    fake = FakeSender()
    notify_mod.register_sender("bark", fake)
    s = _with_channel(_settings(), "bark")
    notify_mod.notify(s, _result(success=True))
    assert len(fake.calls) == 1
