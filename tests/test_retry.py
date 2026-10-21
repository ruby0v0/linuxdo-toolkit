"""重试助手测试。"""

import pytest

from src import retry as retry_mod
from src.retry import with_retry


def test_success_first_attempt() -> None:
    calls = []
    result = with_retry(lambda: calls.append(1) or "ok", what="测试", delay_range=(0.0, 0.0))
    assert result == "ok"
    assert len(calls) == 1


def test_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """前两次失败、第三次成功：共调用 3 次，异常被吞。"""
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _: None)
    counter = {"n": 0}

    def flaky() -> str:
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert with_retry(flaky, what="测试", delay_range=(0.0, 0.0)) == "ok"
    assert counter["n"] == 3


def test_gives_up_after_attempts_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _: None)
    counter = {"n": 0}

    def always_fail() -> None:
        counter["n"] += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        with_retry(always_fail, what="测试", attempts=3, delay_range=(0.0, 0.0))
    assert counter["n"] == 3


def test_on_give_up_callback_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _: None)
    given_up: list[Exception] = []

    def fail() -> None:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        with_retry(fail, what="测试", attempts=1, on_give_up=given_up.append)
    assert len(given_up) == 1


def test_non_matching_exception_not_retried() -> None:
    """不在 exceptions 元组里的异常直接抛出，不重试。"""
    counter = {"n": 0}

    def fail() -> None:
        counter["n"] += 1
        raise KeyError("not retried")

    with pytest.raises(KeyError):
        with_retry(fail, what="测试", exceptions=(ValueError,))
    assert counter["n"] == 1
