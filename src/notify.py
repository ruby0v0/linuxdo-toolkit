"""通知模块：可插拔设计（AC-7）。新增渠道 = 实现 Sender 协议 + 注册表加一行。

发送失败仅 warning，不影响主流程退出码（BR-5）。
"""

from __future__ import annotations

from typing import Protocol

import httpx
from loguru import logger

from src.config import Settings
from src.models import RunResult

TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT = 15


class Sender(Protocol):
    """通知发送协议。"""

    def send(self, settings: Settings, title: str, body: str) -> None: ...


class TelegramSender:
    """Telegram Bot API 发送器。"""

    def send(self, settings: Settings, title: str, body: str) -> None:
        assert settings.notify_telegram_bot_token
        assert settings.notify_telegram_chat_id
        url = f"{TELEGRAM_API}/bot{settings.notify_telegram_bot_token}/sendMessage"
        resp = httpx.post(
            url,
            json={
                "chat_id": settings.notify_telegram_chat_id,
                "text": f"{title}\n\n{body}",
                "disable_web_page_preview": True,
            },
            timeout=SEND_TIMEOUT,
        )
        if resp.status_code != 200:
            # 不回显 token：错误信息截断，仅含 API 返回的描述
            raise RuntimeError(f"Telegram API 返回 {resp.status_code}：{resp.text[:120]}")
        logger.debug("Telegram 通知已发送")


# 渠道注册表：新增渠道在此登记即可
_SENDERS: dict[str, Sender] = {
    "telegram": TelegramSender(),
}


def register_sender(name: str, sender: Sender) -> None:
    """注册新渠道（扩展点）。"""
    _SENDERS[name] = sender


def notify(settings: Settings, result: RunResult) -> None:
    """按配置分发通知；任何失败只记 warning。"""
    if not settings.notify_enabled:
        logger.debug("通知未启用，跳过")
        return
    sender = _SENDERS.get(settings.notify_channel)
    if sender is None:
        logger.warning("未知通知渠道：{}，跳过", settings.notify_channel)
        return
    title = "linuxdo-toolkit 运行报告"
    try:
        sender.send(settings, title, result.to_notification_body())
        logger.info("通知已发送（渠道：{}）", settings.notify_channel)
    except Exception as exc:  # noqa: BLE001 — 通知失败不影响主流程（BR-5）
        logger.warning("通知发送失败（不影响主流程）：{}", exc)
