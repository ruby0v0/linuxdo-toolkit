"""浏览模块测试：mock DrissionPage 标签页桩，不发真实网络请求、不启动浏览器。"""

from typing import Any

import pytest

from src import browse as browse_mod
from src.browse import BrowseStats, browse, fetch_topic_urls
from src.config import Settings


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用真实 sleep，测试瞬时完成。"""
    monkeypatch.setattr(browse_mod.time, "sleep", lambda _: None)


class FakeLink:
    def __init__(self, href: str, text: str) -> None:
        self._href = href
        self._text = text

    def attr(self, name: str) -> str | None:
        return self._href if name == "href" else None

    @property
    def text(self) -> str:
        return self._text


class FakeArea:
    def __init__(self, links: list[FakeLink]) -> None:
        self._links = links

    def eles(self, selector: str) -> list[FakeLink]:
        return self._links


class FakePage:
    """最小 ChromiumPage 桩：首页返回列表区域，可模拟限流/失败。"""

    def __init__(
        self,
        topic_count: int = 3,
        fail_urls: set[str] | None = None,
        body: str = "",
        has_area: bool = True,
    ) -> None:
        self._links = [FakeLink(f"/t/topic-{i}", f"标题{i}") for i in range(topic_count)]
        self.fail_urls = fail_urls or set()
        self.visited: list[str] = []
        self._body = body
        self._has_area = has_area
        self.title = "LINUX DO"
        self.url = "https://linux.do/"
        self.html = f"<html><body>{body}</body></html>"
        self.like_clicked = 0

    class _SetStub:
        def cookies(self, *_: Any) -> None:
            pass

    set = _SetStub()

    def get(self, url: str, **kwargs: Any) -> None:
        if any(f in url for f in self.fail_urls):
            raise RuntimeError(f"navigation failed: {url}")
        self.visited.append(url)

    def ele(self, selector: str, timeout: float = 0) -> Any:
        # 列表区域
        if "list-area" in selector:
            return FakeArea(self._links) if self._has_area else None
        # 点赞按钮：返回可点击对象，parent() 返回未赞容器（无已赞标记）
        if "like" in selector:
            class _Parent:
                def attr(self, name: str) -> str | None:
                    cls = "discourse-reactions-actions can-toggle-reaction"
                    return cls if name == "class" else None

            class _Btn:
                def __init__(self, outer: "FakePage") -> None:
                    self._outer = outer

                def click(self) -> None:
                    self._outer.like_clicked += 1

                def parent(self) -> "_Parent":
                    return _Parent()

            return _Btn(self)
        # 正文
        if "post_1" in selector or "topic-post" in selector or "main-outlet" in selector:
            return True
        return None

    def run_js(self, script: str, *args: Any) -> Any:
        # 判断 webdriver / 正文 / 滚动，均返回合理值
        if "innerText" in script:
            return self._body
        return None

    def get_screenshot(self, **kwargs: Any) -> None:
        pass


def _settings(**extra: str) -> Settings:
    return Settings(**{"LINUXDO_COOKIES": "_t=x", **extra, "_env_file": None})  # type: ignore[arg-type]


def test_fetch_topic_urls_shuffles_and_limits() -> None:
    page = FakePage(topic_count=10)
    topics = fetch_topic_urls(page, 4)  # type: ignore[arg-type]
    assert len(topics) == 4
    assert all(url.startswith("https://linux.do/t/") for url, _ in topics)


def test_fetch_empty_list() -> None:
    """列表为空 → stats.visited == 0（部分失败路径）。"""
    page = FakePage(topic_count=0)
    stats = browse(page, _settings(BROWSE_COUNT="2"))  # type: ignore[arg-type]
    assert stats.visited == 0 and stats.total == 2


def test_fetch_no_area_returns_empty() -> None:
    """找不到列表容器 → 返回空，不抛异常。"""
    page = FakePage(topic_count=3, has_area=False)
    assert fetch_topic_urls(page, 2) == []  # type: ignore[arg-type]


def test_browse_visits_all_topics() -> None:
    page = FakePage(topic_count=5)
    stats = browse(page, _settings(BROWSE_COUNT="3"))  # type: ignore[arg-type]
    assert stats.visited == 3
    assert stats.failed == 0
    assert len(stats.topics) == 3


def test_browse_skips_failed_topic() -> None:
    """单个帖子失败则跳过，其余继续。"""
    page = FakePage(topic_count=4, fail_urls={"/t/topic-2"})
    stats = browse(page, _settings(BROWSE_COUNT="4"))  # type: ignore[arg-type]
    assert stats.visited == 3
    assert stats.failed == 1


def test_like_disabled_by_default() -> None:
    """LIKE_ENABLED 默认关闭 → 不点赞。"""
    page = FakePage(topic_count=3)
    stats = browse(page, _settings(BROWSE_COUNT="3"))
    assert stats.likes_given == 0
    assert page.like_clicked == 0


def test_like_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIKE_ENABLED=true 且概率命中 → 点赞按钮被点击。"""
    monkeypatch.setattr(browse_mod.random, "random", lambda: 0.0)  # 必定命中点赞概率
    page = FakePage(topic_count=3)
    stats = browse(page, _settings(BROWSE_COUNT="2", LIKE_ENABLED="true"))
    assert stats.likes_given >= 1
    assert page.like_clicked >= 1


def test_stats_defaults() -> None:
    stats = BrowseStats()
    assert stats.total == 0 and stats.visited == 0
    assert stats.likes_given == 0
    assert stats.topics == []


def test_scroll_parameters_in_bounds() -> None:
    assert browse_mod.DWELL_RANGE[0] >= 2.0
    assert browse_mod.DWELL_RANGE[1] <= 10.0
    assert browse_mod.SCROLL_STEPS_RANGE[1] <= 8
    assert browse_mod.LIKE_PROBABILITY <= 0.3
    assert browse_mod.MAX_LIKES_PER_RUN <= 3


# ---------------------------------------------------------------------------
# 限流识别
# ---------------------------------------------------------------------------
def test_rate_limited_page_fails_fast() -> None:
    """限流正文应抛 RateLimitError 且快速失败（不重试），而非静默返回空列表。"""
    from src.errors import RateLimitError

    page = FakePage(topic_count=3, body="Too Many Requests")
    with pytest.raises(RateLimitError):
        browse(page, _settings(BROWSE_COUNT="2"))  # type: ignore[arg-type]


def test_cloudflare_challenge_detected() -> None:
    from src.browse import _looks_rate_limited

    assert _looks_rate_limited("Just a moment... Checking your browser") is not None


def test_normal_page_not_flagged() -> None:
    from src.browse import _looks_rate_limited

    assert _looks_rate_limited("最新 话题 列表 LINUX DO") is None
