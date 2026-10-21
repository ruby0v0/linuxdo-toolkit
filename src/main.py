"""入口：配置 → 日志 → 登录 →（可选）浏览 → 摘要 → 通知。

退出码（BR-4）：登录成功且（浏览关闭 或 浏览 ≥1 帖）→ 0；否则 1。
"""

from __future__ import annotations

import argparse
import sys
import time

from loguru import logger

from src import __version__, auth, browse, notify
from src.browser import BrowserSession
from src.config import Settings
from src.http_client import create_client
from src.models import RunResult


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), format=LOG_FORMAT)


LOG_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan> - {message}"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="linux.do 每日访问保持工具")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅登录验证，不浏览、不通知（Q3 决议）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args()


def run(settings: Settings, dry_run: bool = False) -> RunResult:
    """主流程编排。"""
    start = time.monotonic()
    result = RunResult(success=False, browse_enabled=settings.browse_enabled and not dry_run)

    http = create_client()
    try:
        # 1. 认证（BR-1）
        try:
            auth_result = auth.login(settings, http)
        except Exception as exc:
            result.error = f"登录失败：{exc}"
            return result
        result.auth_method = auth_result.method
        result.username = auth_result.username

        if dry_run:
            result.success = True
            return result

        # 2. 浏览（可选）
        if settings.browse_enabled:
            session = BrowserSession(settings)
            try:
                page = session.start(auth_result.cookies)
                stats = browse.browse(page, settings)
                result.browse_total = stats.total
                result.browse_visited = stats.visited
                result.browse_failed = stats.failed
                result.likes_given = stats.likes_given
                result.topics = stats.topics
            finally:
                session.close()

        # 3. 成功判定（BR-4）：浏览开启但 0 帖 → 部分失败
        if settings.browse_enabled and result.browse_visited == 0:
            result.error = "登录成功但 0 帖浏览（列表为空或选择器失效）"
            return result
        result.success = True
        return result
    finally:
        result.duration_seconds = time.monotonic() - start
        http.close()


def main() -> int:
    args = parse_args()
    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as exc:
        print(f"配置校验失败：{exc}", file=sys.stderr)
        return 1
    settings.dry_run = args.dry_run or settings.dry_run
    setup_logging(settings.log_level)
    logger.info("linuxdo-toolkit v{} 启动（dry_run={}）", __version__, settings.dry_run)

    try:
        result = run(settings, dry_run=settings.dry_run)
    except Exception as exc:  # noqa: BLE001 — 顶层兜底：任何未捕获异常转为失败结果
        result = RunResult(success=False, error=str(exc), browse_enabled=settings.browse_enabled)

    logger.info(result.summary)
    if not settings.dry_run:
        notify.notify(settings, result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
