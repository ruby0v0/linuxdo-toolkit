"""浏览行为：抓取帖子列表 → 随机抽取 → 随机滚动 + 停留 → 可选低频点赞（BR-3）。"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from loguru import logger
from playwright.sync_api import Page

from src.config import Settings
from src.errors import RateLimitError
from src.retry import with_retry

BASE_URL = "https://linux.do"

# --- 页面选择器（集中常量，应对站点改版只需改这里） ---
SELECTOR_TOPIC_LIST = "table.topic-list tr.topic-list-item"
SELECTOR_TOPIC_LINK = "a.title.raw-link.raw-topic-link"
SELECTOR_TOPIC_BODY = "#post_1, .topic-post"

# --- 行为参数（区间设计使总时长落在 2~5 分钟：6 帖 × ~(20s 滚动 + 25s 停留) ≈ 4 分钟） ---
DWELL_RANGE = (3.0, 8.0)  # 每屏停留秒数
SCROLL_STEPS_RANGE = (2, 6)  # 每帖滚动屏数
SCROLL_STEP_INTERVAL = (0.8, 2.0)  # 滚动间隔秒数
SCROLL_DISTANCE = (300, 700)  # 单次滚动像素
LIKE_PROBABILITY = 0.3  # 点赞概率上限（LIKE_ENABLED=true 时）
MAX_LIKES_PER_RUN = 3  # 每次运行点赞总数上限

# --- 风控识别（数据中心 IP / Cloudflare 常见拦截特征） ---
# 命中正文关键词即判定为被限流/挑战，用于让上层改用长退避重试而非秒级快速重试
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


def _human_scroll(page: Page) -> None:
    """模拟真实阅读：随机屏数、随机距离、随机间隔的滚动。"""
    steps = random.randint(*SCROLL_STEPS_RANGE)
    for _ in range(steps):
        page.mouse.wheel(0, random.uniform(*SCROLL_DISTANCE))
        time.sleep(random.uniform(*SCROLL_STEP_INTERVAL))


def _dwell(page: Page) -> None:
    time.sleep(random.uniform(*DWELL_RANGE))


def _retry_after_from(response: object) -> float | None:
    """从 Playwright Response 响应头解析 Retry-After（秒），失败返回 None。"""
    headers = getattr(response, "headers", None)
    if not isinstance(headers, dict):
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None  # Retry-After 也可能是 HTTP 日期格式，此处仅处理秒数形式


def _looks_rate_limited(response: object, body_text: str) -> RateLimitError | None:
    """综合 HTTP 状态码与正文判断是否被限流/挑战，命中则返回异常对象。"""
    status = getattr(response, "status", None)
    lowered = (body_text or "").lower()
    hit_keyword = next((kw for kw in _RATE_LIMIT_KEYWORDS if kw in lowered), None)
    if status == 429:
        return RateLimitError(
            "被限流（HTTP 429，Too Many Requests）",
            retry_after=_retry_after_from(response),
        )
    if hit_keyword is not None:
        return RateLimitError(
            f"疑似被风控/Cloudflare 挑战（正文命中 {hit_keyword!r}）",
            retry_after=_retry_after_from(response),
        )
    return None


def _body_text(page: Page) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:  # noqa: BLE001 — 上下文销毁等场景，退化为空串
        return ""


def fetch_topic_urls(page: Page, count: int) -> list[tuple[str, str]]:
    """从 latest 列表收集帖子 (url, title)，随机打散。

    Discourse 是 SPA，页面 load 后内容由 JS 渲染。
    用 JS 轮询等待列表元素出现（避免 networkidle 永不触发的问题）。

    关键：显式检查 HTTP 状态与正文，命中限流/挑战时抛 RateLimitError，
    让上层 with_retry 使用长退避重试，而不是把"429 错误页"误判为"空列表"。
    """
    response = page.goto(f"{BASE_URL}/latest", wait_until="load", timeout=60_000)

    # 先按状态码 / 正文判断是否被限流，命中直接抛（交给上层长退避重试）
    blocked = _looks_rate_limited(response, _body_text(page))
    if blocked is not None:
        _dump_debug(page, prefix="rate-limited")
        raise blocked

    # 轮询等待列表元素出现（最多 20s，每 0.5s 检查一次）
    try:
        page.wait_for_function(
            "document.querySelectorAll('table.topic-list tr.topic-list-item').length > 0",
            timeout=20_000,
        )
    except Exception:  # noqa: BLE001 — 超时/上下文销毁视为列表未就绪
        # 超时后再判定一次：可能是慢速挑战页伪装成空白
        blocked = _looks_rate_limited(response, _body_text(page))
        if blocked is not None:
            _dump_debug(page, prefix="rate-limited")
            raise blocked from None
        logger.warning(
            "等待帖子列表超时：当前 URL={}，标题={}（页面已加载但无列表元素，疑似选择器失效）",
            page.url, page.title(),
        )
        _dump_debug(page, prefix="empty-topic-list")
        return []

    elements = page.query_selector_all(SELECTOR_TOPIC_LIST)
    topics: list[tuple[str, str]] = []
    for el in elements:
        link = el.query_selector(SELECTOR_TOPIC_LINK)
        href = link.get_attribute("href") if link else None
        title = (link.inner_text() if link else "").strip()
        if href:
            topics.append((f"{BASE_URL}{href}" if href.startswith("/") else href, title or href))
    random.shuffle(topics)
    return topics[:count]


def _dump_debug(page: Page, prefix: str) -> None:
    """保存诊断产物（截图 + HTML 片段），用于排查页面实际状态。"""
    try:
        screenshot = f"/tmp/{prefix}.png"
        html_path = f"/tmp/{prefix}.html"
        page.screenshot(path=screenshot)
        html = page.content()
        with open(html_path, "w") as f:
            f.write(html)
        logger.warning("诊断产物：截图 {}，HTML {}（{} 字节）", screenshot, html_path, len(html))
        # 页面正文前 200 字符（判断是否 CF 挑战页）
        body_text = _body_text(page)[:200]
        logger.warning("页面正文预览：{}", body_text.replace("\n", " ")[:200])
    except Exception as exc:  # noqa: BLE001 — 诊断失败不影响主流程
        logger.warning("保存诊断产物失败：{}", exc)


def _maybe_like(page: Page, stats: BrowseStats) -> None:
    """低频点赞：概率命中且未达上限时，点开帖子第一个赞按钮。"""
    if stats.likes_given >= MAX_LIKES_PER_RUN or random.random() > LIKE_PROBABILITY:
        return
    button = page.query_selector(".topic-post:first-child .like-button, button.like")
    if button is None:
        logger.debug("未找到点赞按钮，跳过点赞")
        return
    try:
        button.click()
        stats.likes_given += 1
        logger.info("已点赞第 {} 篇", stats.visited)
    except Exception as exc:  # noqa: BLE001 — 点赞失败不影响浏览
        logger.warning("点赞失败：{}", exc)


def visit_topic(page: Page, url: str, title: str, settings: Settings, stats: BrowseStats) -> None:
    """访问单个帖子：加载 → 滚动 → 停留 →（可选）点赞。失败抛异常由调用方跳过。"""
    with_retry(
        lambda: page.goto(url, wait_until="domcontentloaded", timeout=30_000),
        what=f"打开帖子（{title[:30]}）",
    )
    page.wait_for_selector(SELECTOR_TOPIC_BODY, timeout=10_000)
    _human_scroll(page)
    _dwell(page)
    if settings.like_enabled:
        _maybe_like(page, stats)
    stats.visited += 1
    stats.topics.append(title)
    logger.info("已浏览（{}/{}）：{}", stats.visited, stats.total, title)


def _rate_limit_delay(exc: Exception, attempt: int) -> float:
    """限流退避：优先遵守 Retry-After，否则取长退避区间 + 小抖动。"""
    if isinstance(exc, RateLimitError) and exc.retry_after:
        # 服务端建议等待，加 10% 抖动避免与其它实例同时重试
        return exc.retry_after * random.uniform(1.0, 1.1)
    return random.uniform(*RATE_LIMIT_BACKOFF)


def browse(page: Page, settings: Settings) -> BrowseStats:
    """浏览主流程：抓列表 → 逐帖访问。单帖失败跳过（BR-4）。"""
    stats = BrowseStats(total=settings.browse_count)
    logger.info("开始浏览：计划 {} 帖，点赞={}", settings.browse_count, settings.like_enabled)

    topics = with_retry(
        lambda: fetch_topic_urls(page, settings.browse_count),
        what="获取帖子列表",
        exceptions=(RateLimitError,),  # 仅对限流类异常重试；返回空列表视为成功不再重试
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
        # 帖子之间的自然间隔
        time.sleep(random.uniform(1.0, 3.0))

    logger.info(
        "浏览完成：成功 {} / 失败 {} / 点赞 {}",
        stats.visited, stats.failed, stats.likes_given,
    )
    return stats
