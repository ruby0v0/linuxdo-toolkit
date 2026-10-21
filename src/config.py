"""配置模块：pydantic-settings 读取环境变量，fail-fast 校验。

所有凭据与开关均由环境变量驱动；本地开发可放在 .env 文件中（已被 .gitignore 排除）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NotifyChannel(StrEnum):
    """支持的通知渠道。"""

    TELEGRAM = "telegram"


class Settings(BaseSettings):
    """工具运行配置。字段名即环境变量名。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 认证 ---
    linuxdo_cookies: str | None = None
    linuxdo_username: str | None = None
    linuxdo_password: str | None = None

    # --- 浏览行为 ---
    browse_enabled: bool = True
    browse_count: int = Field(default=6, ge=1, le=20)
    like_enabled: bool = False
    headless: bool = True

    # --- 通知 ---
    notify_enabled: bool = False
    notify_channel: NotifyChannel = NotifyChannel.TELEGRAM
    notify_telegram_bot_token: str | None = None
    notify_telegram_chat_id: str | None = None

    # --- 其他 ---
    log_level: str = "INFO"
    dry_run: bool = False  # 仅登录验证，不浏览、不通知（通常由 CLI 参数注入）

    # --- 便捷判断 ---
    @property
    def has_cookies(self) -> bool:
        return bool(self.linuxdo_cookies and self.linuxdo_cookies.strip())

    @property
    def has_credentials(self) -> bool:
        return bool(self.linuxdo_username and self.linuxdo_password)

    @model_validator(mode="after")
    def validate_auth_source(self) -> Settings:
        """至少提供一种登录方式。"""
        if not self.has_cookies and not self.has_credentials:
            raise ValueError(
                "缺少登录凭据：请提供 LINUXDO_COOKIES，或 LINUXDO_USERNAME + LINUXDO_PASSWORD"
            )
        return self

    @model_validator(mode="after")
    def validate_notify_config(self) -> Settings:
        """启用通知时必须提供对应渠道的完整配置。"""
        if self.notify_enabled and self.notify_channel is NotifyChannel.TELEGRAM:
            if not self.notify_telegram_bot_token or not self.notify_telegram_chat_id:
                raise ValueError(
                    "NOTIFY_ENABLED=true 且渠道为 telegram 时，"
                    "必须提供 NOTIFY_TELEGRAM_BOT_TOKEN 和 NOTIFY_TELEGRAM_CHAT_ID"
                )
        return self
