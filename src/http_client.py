"""HTTP 客户端适配器：curl-cffi 优先（浏览器 TLS 指纹），异常时降级 httpx（BR-2）。"""

from __future__ import annotations

from typing import Any, Protocol

from loguru import logger


class HttpResponse(Protocol):
    """统一响应接口（curl-cffi 与 httpx 均满足 status_code / text / json）。"""

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self, **kwargs: Any) -> Any: ...


class HttpClient(Protocol):
    """统一 HTTP 客户端接口。"""

    def get(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def post(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def close(self) -> None: ...


class CurlCffiClient:
    """curl-cffi 封装，带 Chrome 浏览器 TLS 指纹。"""

    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        from curl_cffi import requests as curl_requests

        self._session = curl_requests.Session(impersonate="chrome")
        if cookies:
            self._session.cookies.update(cookies)

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self._session.get(url, timeout=30, **kwargs)  # type: ignore[return-value]

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return self._session.post(url, timeout=30, **kwargs)  # type: ignore[return-value]

    def close(self) -> None:
        self._session.close()


class HttpxClient:
    """httpx 封装（降级方案，无浏览器指纹）。"""

    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        import httpx

        self._client = httpx.Client(
            timeout=30,
            cookies=cookies,
            headers={"User-Agent": _FALLBACK_UA},
            follow_redirects=True,
        )

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self._client.get(url, **kwargs)  # type: ignore[return-value]

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return self._client.post(url, **kwargs)  # type: ignore[return-value]

    def close(self) -> None:
        self._client.close()


_FALLBACK_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def create_client(cookies: dict[str, str] | None = None) -> HttpClient:
    """工厂：优先 curl-cffi，初始化失败降级 httpx 并记录原因。"""
    try:
        client: HttpClient = CurlCffiClient(cookies)
        logger.debug("HTTP 客户端：curl-cffi（Chrome 指纹）")
        return client
    except Exception as exc:  # noqa: BLE001 — 降级路径需捕获导入/初始化的所有异常
        logger.warning("curl-cffi 初始化失败（{}），降级为 httpx", exc)
        return HttpxClient(cookies)
