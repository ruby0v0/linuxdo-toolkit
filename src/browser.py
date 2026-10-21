"""Playwright 生命周期封装：启动 Chromium → 注入会话 Cookie → 提供 Page。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from loguru import logger
from playwright.sync_api import sync_playwright

from src.config import Settings

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


# 与 http_client.py 的降级 UA 保持同一 Chrome 大版本，减少指纹割裂
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserSession:
    """管理 Playwright 启动/关闭与已登录浏览上下文。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def start(self, cookies: dict[str, str]) -> Page:
        """启动浏览器，注入会话 Cookie，返回已登录的 Page。"""
        logger.info("启动 Chromium（headless={}）", self._settings.headless)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._settings.headless)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
            timezone_id="Asia/Shanghai",
        )
        if cookies:
            self._context.add_cookies(
                [
                    {"name": name, "value": value, "domain": ".linux.do", "path": "/"}
                    for name, value in cookies.items()
                ]
            )
            logger.info("已注入 {} 个会话 Cookie", len(cookies))
        return self._context.new_page()

    def close(self) -> None:
        """逆序关闭，单个环节失败不阻塞后续清理。"""
        for closer, name in (
            (lambda: self._context.close() if self._context else None, "context"),
            (lambda: self._browser.close() if self._browser else None, "browser"),
            (lambda: self._pw.stop() if self._pw else None, "playwright"),
        ):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001 — 清理路径尽力而为
                logger.warning("关闭 {} 失败：{}", name, exc)


def open_page(settings: Settings, cookies: dict[str, str]) -> Iterator[Page]:
    """上下文管理器：进入拿 Page，退出时清理浏览器资源。"""
    session = BrowserSession(settings)
    try:
        yield session.start(cookies)
    finally:
        session.close()
