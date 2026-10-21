"""浏览模块测试：mock page 桩，不发真实网络请求、不启动浏览器。"""

from typing import Any

import pytest

from src import browse as browse_mod
from src.browse import BrowseStats, browse, fetch_topic_urls
from src.config import Settings


# 全模块禁用真实 sleep（滚动间隔/停留/帖子间隔），测试瞬时完成
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browse_mod.time, "sleep", lambda _: None)


class FakeElement:
    def __init__(self, href: str, text: str) -> None:
        self._href = href
        self._text = text

    def get_attribute(self, name: str) -> str | None:
        return self._href if name == "href" else None

    def inner_text(self) -> str:
        return self._text

    def query_selector(self, selector: str) -> "FakeElement | None":
        # 行内查询标题链接：返回自身（桩不区分选择器细节）
        return self if "title" in selector or "topic-link" in selector else None


class FakePage:
    """最小 page 桩：latest 列表返回固定元素，goto 记录访问。"""

    def __init__(self, topic_count: int = 3, fail_urls: set[str] | None = None) -> None:
        self._topics = [
            FakeElement(f"/t/topic-{i}", f"标题{i}") for i in range(topic_count)
        ]
        self.fail_urls = fail_urls or set()
        self.visited: list[str] = []
        self.wheel_calls = 0
        self.url = "https://linux.do/latest"
        self._title = "LINUX DO"

    def title(self) -> str:
        return self._title

    def goto(self, url: str, **kwargs: Any) -> None:
        if any(f in url for f in self.fail_urls):
            raise RuntimeError(f"navigation failed: {url}")
        self.visited.append(url)

    def wait_for_function(self, expression: str, **kwargs: Any) -> None:
        pass  # 桩直接视为条件满足

    def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        pass

    def query_selector_all(self, selector: str) -> list[Any]:
        if "topic-list" in selector:
            return self._topics  # type: ignore[return-value]
        return []

    def query_selector(self, selector: str) -> None:
        return None  # 无点赞按钮

    class _FakeMouse:
        def wheel(self, *_: Any) -> None:
            pass

    mouse = _FakeMouse()


def _settings(**extra: str) -> Settings:
    return Settings(**{"LINUXDO_COOKIES": "_t=x", **extra, "_env_file": None})  # type: ignore[arg-type]


def test_fetch_topic_urls_shuffles_and_limits() -> None:
    page = FakePage(topic_count=10)
    topics = fetch_topic_urls(page, 4)  # type: ignore[arg-type]
    assert len(topics) == 4
    assert all(url.startswith("https://linux.do/t/") for url, _ in topics)


def test_fetch_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """边界条件 2：列表为空 → stats.visited == 0（部分失败路径）。"""
    page = FakePage(topic_count=0)
    stats = browse(page, _settings(BROWSE_COUNT="2"))  # type: ignore[arg-type]
    assert stats.visited == 0 and stats.total == 2


def test_browse_visits_all_topics() -> None:
    page = FakePage(topic_count=5)
    stats = browse(page, _settings(BROWSE_COUNT="3"))  # type: ignore[arg-type]
    assert stats.visited == 3
    assert stats.failed == 0
    assert len(stats.topics) == 3


def test_browse_skips_failed_topic() -> None:
    """BR-4：单帖失败跳过，其余继续。"""
    page = FakePage(topic_count=4, fail_urls={"/t/topic-2"})
    stats = browse(page, _settings(BROWSE_COUNT="4"))  # type: ignore[arg-type]
    assert stats.visited == 3
    assert stats.failed == 1


def test_like_disabled_by_default() -> None:
    """AC-6：LIKE_ENABLED 默认关闭，likes_given 恒为 0。"""
    page = FakePage(topic_count=3)
    stats = browse(page, _settings(BROWSE_COUNT="3", LIKE_ENABLED="true"))  # type: ignore[arg-type]
    assert stats.likes_given == 0


def test_stats_defaults() -> None:
    stats = BrowseStats()
    assert stats.total == 0 and stats.visited == 0
    assert stats.likes_given == 0
    assert stats.topics == []


def test_scroll_parameters_in_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """滚动参数区间收敛检查（防手滑把区间改出合理范围）。"""
    assert browse_mod.DWELL_RANGE[0] >= 2.0
    assert browse_mod.DWELL_RANGE[1] <= 10.0
    assert browse_mod.SCROLL_STEPS_RANGE[1] <= 8
    assert browse_mod.LIKE_PROBABILITY <= 0.3
    assert browse_mod.MAX_LIKES_PER_RUN <= 3
