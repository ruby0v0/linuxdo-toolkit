"""浏览器生命周期封装（DrissionPage + 真 Chrome）：启动 → 注入会话 Cookie → 提供标签页。

相较 Playwright 自带 Chromium，DrissionPage 直接驱动真实 Chrome，
指纹更接近真人，配合真 Chrome 的 UA 可显著降低被 Cloudflare / 数据中心 IP 识别的概率。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import TYPE_CHECKING

from loguru import logger

from src.config import Settings

if TYPE_CHECKING:
    from DrissionPage import Chromium, ChromiumPage


# 与 http_client.py 的降级 UA 保持同一 Chrome 大版本，减少指纹割裂
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 各平台 Chrome 默认可执行文件路径（未显式配置 CHROME_PATH 时按此自动探测）
_DEFAULT_CHROME_PATHS = {
    "darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "linux": "/usr/bin/google-chrome",
    "win32": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}


def _resolve_chrome_path(configured: str | None) -> str | None:
    """确定 Chrome 可执行文件路径：优先显式配置，否则按平台默认探测，找不到返回 None。"""
    if configured and configured.strip():
        return configured.strip()
    import os

    candidate = _DEFAULT_CHROME_PATHS.get(sys.platform)
    if candidate and os.path.exists(candidate):
        return candidate
    return None


def _build_options(settings: Settings) -> ChromiumOptions:  # noqa: F821
    """构造 ChromiumOptions：路径 / 无头 / 反指纹参数 / UA / 代理。"""
    from DrissionPage import ChromiumOptions

    co = ChromiumOptions()
    chrome_path = _resolve_chrome_path(settings.chrome_path)
    if chrome_path:
        co.set_browser_path(chrome_path)
        logger.debug("使用 Chrome 路径：{}", chrome_path)

    co.headless(settings.headless)
    co.incognito(True)  # 干净上下文，避免脏状态干扰
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--disable-features=IsolateOrigins,site-per-process")
    co.set_user_agent(USER_AGENT)
    co.set_argument("--lang=zh-CN")

    # 可选出口代理：格式 host:port 或 http://host:port
    proxy = (settings.playwright_proxy or "").strip()
    if proxy:
        co.set_proxy(proxy)
        logger.info("浏览器启用代理：{}", proxy)
    return co


class BrowserSession:
    """管理 DrissionPage 启动/关闭与已登录浏览上下文。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser: Chromium | None = None
        self._page: ChromiumPage | None = None

    def start(self, cookies: dict[str, str]) -> ChromiumPage:  # noqa: F821
        """启动 Chrome，注入会话 Cookie，返回已登录的标签页。"""
        from DrissionPage import Chromium

        logger.info("启动 Chrome（headless={}）", self._settings.headless)
        self._browser = Chromium(_build_options(self._settings))
        self._page = self._browser.latest_tab

        if cookies:
            cookie_list = [
                {"name": name, "value": value, "domain": ".linux.do", "path": "/"}
                for name, value in cookies.items()
            ]
            self._page.set.cookies(cookie_list)
            logger.info("已注入 {} 个会话 Cookie", len(cookies))
        return self._page

    def close(self) -> None:
        """关闭浏览器，失败不阻塞。"""
        try:
            if self._browser is not None:
                self._browser.quit()
        except Exception as exc:  # noqa: BLE001 — 清理路径尽力而为
            logger.warning("关闭浏览器失败：{}", exc)


def open_page(settings: Settings, cookies: dict[str, str]) -> Iterator[ChromiumPage]:  # noqa: F821
    """上下文管理器：进入拿标签页，退出时清理浏览器资源。"""
    session = BrowserSession(settings)
    try:
        yield session.start(cookies)
    finally:
        session.close()
