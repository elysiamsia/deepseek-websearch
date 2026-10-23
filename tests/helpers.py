"""测试公共工具：假客户端、假存储、假时钟与 Runtime 构造。"""

from __future__ import annotations

from datetime import datetime

from tests import framework_stub

runtime_mod = framework_stub.import_component("core.runtime")
models = framework_stub.import_component("core.models")

#: 测试统一使用的「当前时间」。
NOW = datetime(2026, 10, 23, 21, 0, 0)


class Clock:
    """可手动推进的单调时钟。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeClient:
    """假客户端：记录调用，返回预设结果或抛出预设异常。"""

    def __init__(self, results=None, error=None, title="标题", content="正文", fetch_error=None):
        self.results = results if results is not None else []
        self.error = error
        self.title = title
        self.content = content
        self.fetch_error = fetch_error
        self.search_calls = 0
        self.fetch_calls = 0
        self.last_search = {}
        self.last_fetch = ""
        self.closed = False

    async def search(self, query, *, count=10, need_content=False, ori_question=""):
        """记录并返回预设结果。"""

        self.search_calls += 1
        self.last_search = {
            "query": query,
            "count": count,
            "need_content": need_content,
            "ori_question": ori_question,
        }
        if self.error:
            raise self.error
        return list(self.results)

    async def fetch(self, url):
        """记录并返回预设正文。"""

        self.fetch_calls += 1
        self.last_fetch = url
        if self.fetch_error:
            raise self.fetch_error
        return self.title, self.content

    async def aclose(self):
        """标记关闭。"""

        self.closed = True


class FakeStore:
    """假持久化实现。"""

    def __init__(self, data=None):
        self.data = data
        self.saves = []
        self.loads = 0

    async def load(self):
        """读取预设数据。"""

        self.loads += 1
        return self.data

    async def save(self, data):
        """记录写入。"""

        self.saves.append(dict(data))
        self.data = dict(data)


def sample_result(title="标题", url="https://example.com/a"):
    """构造一条搜索结果。"""

    return models.SearchResult(
        title=title,
        url=url,
        site_name="示例站",
        date="2026-08-27T16:00:00+00:00",
        summary="摘要内容",
        snippet="短摘要",
        authority=3,
        rerank_score=2.0,
    )


class FakeLLM:
    """假的小模型调用：记录入参并返回预设文本。"""

    def __init__(self, output="压缩后的正文", error=None):
        self.output = output
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, system: str, user: str, max_tokens: int) -> str:
        """记录并返回。"""

        self.calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens}
        )
        if self.error:
            raise self.error
        return self.output


def build_runtime(**overrides):
    """构造 (Runtime, FakeClient, FakeStore, Clock)。"""

    llm_call = overrides.pop("llm_call", None)
    config = framework_stub.build_config(**overrides)
    clock = Clock()
    store = FakeStore()
    client = FakeClient(results=[sample_result()])
    runtime = runtime_mod.Runtime(
        config,
        client=client,
        store=store,
        clock=clock,
        now=lambda: NOW,
        llm_call=llm_call,
    )
    return runtime, client, store, clock


class FakePlugin:
    """只提供 get_runtime 的插件替身。"""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def get_runtime(self):
        """返回预设 Runtime。"""

        return self.runtime
