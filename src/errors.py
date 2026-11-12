"""自定义异常：用于区分"可重试的限流"与"真正的失败"。"""

from __future__ import annotations


class RateLimitError(Exception):
    """被站点限流/风控拦截（HTTP 429、Cloudflare 挑战页等）。

    与普通异常区分开，以便上层使用"长退避"而非普通的秒级快速重试。
    ``retry_after`` 携带服务端建议的等待秒数（若响应头提供 Retry-After）。
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
