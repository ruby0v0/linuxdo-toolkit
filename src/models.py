"""运行结果数据模型（日志摘要与通知内容共用）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunResult:
    """一次完整运行的结果。"""

    success: bool
    auth_method: str | None = None  # "cookie" | "password"
    username: str | None = None
    browse_enabled: bool = True
    browse_visited: int = 0
    browse_total: int = 0
    browse_failed: int = 0
    likes_given: int = 0
    topics: list[str] = field(default_factory=list)
    error: str | None = None  # 失败原因（脱敏后）
    duration_seconds: float = 0.0

    @property
    def summary(self) -> str:
        """单行摘要（日志与通知共用）。"""
        if not self.success:
            return f"运行失败 | 原因：{self.error} | 耗时 {self.duration_seconds:.0f}s"
        browse_part = (
            f"浏览={self.browse_visited}/{self.browse_total}"
            if self.browse_enabled
            else "浏览=关闭"
        )
        return (
            f"运行成功 | 登录={self.auth_method} | 用户={self.username} | "
            f"{browse_part} | 点赞={self.likes_given} | "
            f"耗时 {self.duration_seconds:.0f}s"
        )

    def to_notification_body(self) -> str:
        """通知正文（多行版，失败时只含脱敏原因）。"""
        lines = [f"状态：{'✅ 成功' if self.success else '❌ 失败'}"]
        if self.success:
            lines.append(f"登录方式：{self.auth_method}")
            lines.append(f"用户：{self.username}")
            if self.browse_enabled:
                lines.append(
                    f"浏览：{self.browse_visited}/{self.browse_total}"
                    f"（失败 {self.browse_failed}）"
                )
                lines.append(f"点赞：{self.likes_given}")
        else:
            lines.append(f"原因：{self.error}")
        lines.append(f"耗时：{self.duration_seconds:.0f}s")
        return "\n".join(lines)
