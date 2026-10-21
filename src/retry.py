"""通用重试助手：有限次数重试 + 随机延迟（BR-4）。"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from loguru import logger

T = TypeVar("T")

# 重试参数（BR-4：最多 3 次，随机延迟 1.5~4.0s）
MAX_ATTEMPTS = 3
DELAY_RANGE = (1.5, 4.0)


def with_retry(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = MAX_ATTEMPTS,
    delay_range: tuple[float, float] = DELAY_RANGE,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    no_retry: tuple[type[Exception], ...] = (),  # 命中即不重试（如凭据错误）
    on_give_up: Callable[[Exception], None] | None = None,
) -> T:
    """执行 fn，失败后带随机延迟重试，最终失败抛出原异常。

    Args:
        fn: 要执行的可调用对象。
        what: 操作描述，用于日志。
        attempts: 总尝试次数（含首次），最少 1。
        delay_range: 重试延迟区间（秒），每次 uniform 取值。
        exceptions: 需要重试的异常类型。
        no_retry: 命中这些异常时立即失败，不重试（永久性错误）。
        on_give_up: 最终失败时的回调（如发通知），不影响异常传播。
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except exceptions as exc:
            if isinstance(exc, no_retry):
                logger.warning("{} 失败（不可重试）：{}", what, exc)
                raise exc from None
            last_error = exc
            if attempt == attempts:
                break
            delay = random.uniform(*delay_range)
            logger.warning(
                "{} 失败（第 {}/{} 次）：{}，{:.1f}s 后重试",
                what, attempt, attempts, exc, delay,
            )
            time.sleep(delay)
    assert last_error is not None  # attempts >= 1 时必达
    if on_give_up is not None:
        on_give_up(last_error)
    raise last_error
