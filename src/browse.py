"""浏览行为（DrissionPage 版）：抓列表 → 随机抽取 → 滚动停留 → 可选低频点赞（BR-3）。"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.config import Settings
from src.retry import with_retry

if TYPE_CHECKING:
    from DrissionPage import ChromiumPage

BASE_URL = "https://linux.do"

# --- 页面选择器（集中常量，应对站点改版只需改这里） ---
LIST_AREA_ID = "#list-area"  # 首页帖子列表容器
SELECTOR_TOPIC_LINK = "css:a.raw-topic-link"  # 列表内的帖子标题链接
SELECTOR_TOPIC_BODY = "css:#post_1, .topic-post, #main-outlet"  # 帖子详情正文
SELECTOR_LIKE_BUTTON = "css:.discourse-reactions-reaction-button, button.like, .like-button"

# --- 行为参数（区间设计使总时长落在 2~5 分钟） ---
DWELL_RANGE = (3.0, 8.0)  # 每屏停留秒数
SCROLL_STEPS_RANGE = (2, 6)  # 每帖滚动屏数
SCROLL_STEP_INTERVAL = (0.8, 2.0)  # 滚动间隔秒数
SCROLL_DISTANCE = (300, 700)  # 单次滚动像素
LIKE_PROBABILITY = 0.3  # 点赞概率上限（LIKE_ENABLED=true 时）
MAX_LIKES_PER_RUN = 3  # 每次运行点赞总数上限

# --- 风控识别（数据中心 IP / Cloudflare 常见拦截特征） ---
_RATE_LIMIT_KEYWORDS = (
    "too many requests",
    "rate limit",
    "just a moment",  # Cloudflare managed challenge
    "attention required",  # Cloudflare block
    "checking your browser",
)

# 命中限流时的长退避区间（秒）：429 想立刻重试基本都会再被拒，必须等
RATE_LIMIT_BACKOFF = (30.0, 90.0)


@dataclass
class BrowseStats:
    """浏览统计。"""

    total: int = 0  # 计划浏览帖数
    visited: int = 0  # 实际成功访问
    failed: int = 0  # 失败跳过
    likes_given: int = 0
    topics: list[str] = field(default_factory=list)  # 已访问帖子标题（日志/通知用）


def _human_scroll(page: ChromiumPage) -> None:
    """模拟真实阅读：随机屏数、随机距离、随机间隔的滚动。"""
    steps = random.randint(*SCROLL_STEPS_RANGE)
    for _ in range(steps):
        page.run_js(f"window.scrollBy(0, {random.uniform(*SCROLL_DISTANCE):.0f});")
        time.sleep(random.uniform(*SCROLL_STEP_INTERVAL))


def _dwell() -> None:
    time.sleep(random.uniform(*DWELL_RANGE))


def _body_text(page: ChromiumPage) -> str:
    """取页面可见正文（用于风控关键词判断），失败退化为空串。"""
    try:
        return page.run_js("return document.body ? document.body.innerText : ''") or ""
    except Exception:  # noqa: BLE001 — 上下文异常退化为空串
        return ""


def _looks_rate_limited(body_text: str) -> str | None:
    """正文命中限流/挑战关键词时返回命中词，否则 None。"""
    lowered = (body_text or "").lower()
    return next((kw for kw in _RATE_LIMIT_KEYWORDS if kw in lowered), None)


def _rate_limit_delay(exc: Exception, attempt: int) -> float:
    """限流退避：取长退避区间 + 抖动，避免与其它实例同时重试。"""
    from src.errors import RateLimitError

    if isinstance(exc, RateLimitError) and exc.retry_after:
        return exc.retry_after * random.uniform(1.0, 1.1)
    return random.uniform(*RATE_LIMIT_BACKOFF)


def _dump_debug(page: ChromiumPage, prefix: str) -> None:
    """保存诊断产物（截图 + HTML），用于排查页面实际状态。"""
    try:
        screenshot = f"/tmp/{prefix}.png"
        html_path = f"/tmp/{prefix}.html"
        page.get_screenshot(path=screenshot, full_page=False)
        html = page.html
        with open(html_path, "w") as f:
            f.write(html)
        logger.warning("诊断产物：截图 {}，HTML {}（{} 字节）", screenshot, html_path, len(html))
        body_text = _body_text(page)[:200]
        logger.warning("页面正文预览：{}", body_text.replace("\n", " ")[:200])
    except Exception as exc:  # noqa: BLE001 — 诊断失败不影响主流程
        logger.warning("保存诊断产物失败：{}", exc)


def fetch_topic_urls(page: ChromiumPage, count: int) -> list[tuple[str, str]]:
    """访问首页 → 从 #list-area 抓取帖子 (url, title)，随机打散。

    采用"首页 → 列表"而非直接 /latest：首页是正常浏览入口，被 Discourse 限流程度
    通常低于 /latest 列表接口。命中限流/挑战页则抛异常由上层长退避重试。
    """
    page.get(BASE_URL)
    body = _body_text(page)
    hit = _looks_rate_limited(body)
    if hit is not None:
        _dump_debug(page, prefix="rate-limited")
        from src.errors import RateLimitError

        raise RateLimitError(f"疑似被风控/Cloudflare 挑战（正文命中 {hit!r}）")

    area = page.ele(LIST_AREA_ID, timeout=20)
    if not area:
        # 超时兜底再判一次限流，否则视为列表未就绪
        hit = _looks_rate_limited(_body_text(page))
        if hit is not None:
            _dump_debug(page, prefix="rate-limited")
            from src.errors import RateLimitError

            raise RateLimitError(f"疑似被风控/Cloudflare 挑战（正文命中 {hit!r}）")
        logger.warning("未找到帖子列表容器 {}（页面标题：{}）", LIST_AREA_ID, page.title)
        _dump_debug(page, prefix="empty-topic-list")
        return []

    links = area.eles(SELECTOR_TOPIC_LINK)
    topics: list[tuple[str, str]] = []
    for a in links:
        href = a.attr("href")
        if not href:
            continue
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        title = (a.text or "").strip() or href
        topics.append((url, title))
    random.shuffle(topics)
    return topics[:count]


# 已赞会体现在 reaction 容器（按钮父节点）的 class 上：
#   - has-reacted / has-used-main-reaction：我点过主表情
#   - custom-reaction-used：我用过自定义表情
# 命中任一即视为已赞，避免误点导致"取消赞"
_LIKED_CLASS_MARKERS = ("has-reacted", "has-used-main-reaction", "custom-reaction-used")


def _already_liked(button: Any) -> bool:
    """判断该帖是否已点赞（读按钮父容器的 class 标记）。"""
    try:
        parent = button.parent()
        cls = (parent.attr("class") if parent else "") or ""
    except Exception:  # noqa: BLE001 — DOM 异常时保守认为"已赞"，不点
        return True
    return any(marker in cls for marker in _LIKED_CLASS_MARKERS)


def _maybe_like(page: ChromiumPage, stats: BrowseStats) -> None:
    """低频点赞：概率命中且未达上限时，点开帖子第一个赞按钮（已赞则跳过，避免取消赞）。"""
    if stats.likes_given >= MAX_LIKES_PER_RUN or random.random() > LIKE_PROBABILITY:
        return
    button = page.ele(SELECTOR_LIKE_BUTTON, timeout=3)
    if not button:
        logger.debug("未找到点赞按钮，跳过点赞")
        return
    if _already_liked(button):
        logger.debug("该帖已点赞，跳过（避免取消赞）")
        return
    try:
        button.click()
        stats.likes_given += 1
        logger.info("已点赞第 {} 篇", stats.visited)
    except Exception as exc:  # noqa: BLE001 — 点赞失败不影响浏览
        logger.warning("点赞失败：{}", exc)


def visit_topic(
    page: ChromiumPage, url: str, title: str, settings: Settings, stats: BrowseStats
) -> None:
    """访问单个帖子：加载 → 滚动 → 停留 →（可选）点赞。失败抛异常由调用方跳过。"""
    with_retry(lambda: page.get(url), what=f"打开帖子（{title[:30]}）")
    # 等待正文出现（SLA，超时不致命——文末照样继续滚动）
    page.ele(SELECTOR_TOPIC_BODY, timeout=10)
    _human_scroll(page)
    _dwell()
    if settings.like_enabled:
        _maybe_like(page, stats)
    stats.visited += 1
    stats.topics.append(title)
    logger.info("已浏览（{}/{}）：{}", stats.visited, stats.total, title)


def browse(page: ChromiumPage, settings: Settings) -> BrowseStats:
    """浏览主流程：抓列表 → 逐帖访问。单帖失败跳过（BR-4）。"""
    from src.errors import RateLimitError

    stats = BrowseStats(total=settings.browse_count)
    logger.info("开始浏览：计划 {} 帖，点赞={}", settings.browse_count, settings.like_enabled)

    topics = with_retry(
        lambda: fetch_topic_urls(page, settings.browse_count),
        what="获取帖子列表",
        exceptions=(RateLimitError,),  # 仅对限流类异常重试；空列表视为成功不再重试
        delay_fn=_rate_limit_delay,
        on_give_up=lambda exc: logger.error(
            "获取帖子列表最终失败（已重试），疑似出口 IP 被限流/风控：{}", exc
        ),
    )
    if not topics:
        logger.warning("帖子列表为空，可能页面结构变更或被风控")
        return stats

    for url, title in topics:
        try:
            visit_topic(page, url, title, settings, stats)
        except Exception as exc:  # noqa: BLE001 — 单帖失败跳过
            stats.failed += 1
            logger.warning("帖子访问失败，跳过：{}（{}）", title[:40], exc)
        time.sleep(random.uniform(1.0, 3.0))

    logger.info(
        "浏览完成：成功 {} / 失败 {} / 点赞 {}", stats.visited, stats.failed, stats.likes_given
    )
    return stats
