# 架构说明

## 设计目标

1. **只吃搜索服务**：仅 `/v1/tools/search` 与 `/v1/tools/fetch`，不碰对话与识图。
2. **可预测的花费**：缓存、配额、费用统计都是为了让「每次调用值多少钱」看得见。
3. **组件要薄**：Tool / Command / Service 只做参数整理与渲染，业务全在 `core/`。
4. **可脱离框架测试**：`core/` 不 import 任何 `src.*`（唯一例外是 LRU 里没有、而是 `Runtime` 的 `UsageStore` 懒导入 `storage_api`），因此测试用替身即可全量覆盖。

## 分层

```text
components/            ← 框架适配层（只依赖 src.app.plugin_system.*）
   tools.py      deepseek_search / deepseek_fetch
   command.py    /ds
   service.py    websearch
        │  await plugin.get_runtime()
        ▼
core/runtime.py         ← 编排层：cache → budget → client → usage → persist
        │
        ├── core/client.py      HTTP、端点白名单、重试、错误映射
        ├── core/tls.py         进程内共享 SSLContext（证书只解析一次）
        ├── core/query.py       query 清洗 + 过滤语法拼装
        ├── core/cache.py       TTL + LRU
        ├── core/usage.py       计数与费用（纯逻辑，无 I/O）
        ├── core/formatting.py  Markdown / 纯文本
        ├── core/models.py      不可变 dataclass
        └── core/errors.py      异常 + 中文修复建议
```

## 一次搜索的完整链路

```text
LLM/tool call
   │  query + count + need_content + 过滤条件
   ▼
Runtime.search
   │  1. build_query(...)        清洗、拼 site:/lang:/after:/before:，校验 128 字符上限
   │  2. _resolve_count(...)     夹到 [1, min(max_count, 50)]
   │  3. cache.get(key)          命中 → 记 cache_hit，直接返回（不计费）
   │  4. _check_budget()         当日配额
   │  5. client.search(...)      POST /v1/tools/search
   │  6. usage.record_success    （空结果记 record_empty，不计费）
   │  7. cache.set(key, result)
   │  8. _persist()              30 秒节流写盘
   ▼
SearchOutcome → formatting → LLM
```

开了 `[search].fetch_top`（或工具参数 `fetch_top`）时，拿到结果后再对前 N 条 URL 调
`Runtime.fetch_many`，把正文一并返回，省掉模型「先看列表、再逐条 fetch」的多轮往返。

## 性能与并发

v1.4.0 的提速都发生在「等」上：检索语义、结果格式与计费口径都不变，改的是准备成本与串行等待。

### 冷启动：证书只解析一次

`httpx` 默认用 `ssl.create_default_context(cafile=certifi.where())`，CPython 会按文件路径
逐张加载 120 个根证书，本机实测 850–890 ms；同一份 PEM 改用 `cadata` 传入只要约 20 ms。
于是把 TLS 上下文做成进程内单例：

```text
core/tls.py
   ├─ get_shared_ssl_context()   双检锁 + 模块级缓存；构建失败记 _FAILED，之后直接返回 None
   │     └─ ssl.SSLContext(PROTOCOL_TLS_CLIENT) + load_verify_locations(cadata=pem)
   └─ reset_ssl_context_cache()  仅供测试
        │
        ▼
core/client.py  _httpx_post
   ├─ verify_ssl = false              → verify=False（与旧行为一致）
   ├─ reuse_ssl_context = true        → verify = 共享 ctx（拿不到就回退 True）
   └─ reuse_ssl_context = false       → verify=True（httpx 自己建，旧行为）
```

只信任 certifi 的根，不额外信任系统根，与 httpx 原行为一致；任何异常都回退 `verify=True`，
不会因为优化把请求弄挂。配置项 `[api].reuse_ssl_context`。

### 预热：把准备成本挪到启动

`plugin.py::on_plugin_loaded` 在 `[plugin].prewarm = true` 时起一个后台任务：

```text
asyncio.create_task(_prewarm())
   ├─ await asyncio.to_thread(get_shared_ssl_context)   证书解析不占事件循环
   ├─ runtime._ensure_client()                          建 httpx.AsyncClient
   └─ 记录耗时；任何异常只 warning                        不影响插件加载
```

刻意**不做网络预热**：一次真实请求要 ¥0.005，还会污染用量统计；而 TLS 握手只要 ~53 ms。
Key 缺失时预热失败只打 warning，与原来的「首次调用才报错」一致。
预热入口在 `plugin.py`，批量入口在 `Runtime`，组件层只负责参数整理与渲染。

### 批量抓取：Runtime.fetch_many

```text
Runtime.fetch_many(urls, max_chars=...)
   ├─ 去重、保序
   ├─ 超过 [fetch].max_batch → 截断（并在结果里说明）
   ├─ asyncio.Semaphore([fetch].max_batch_concurrency)
   ├─ asyncio.gather(..., return_exceptions=True)
   └─ list[BatchFetchItem]：每条是 outcome 或 error，单条失败不影响其它条
        │
        ▼
components/tools.py   deepseek_fetch(urls=[...])
components/render.py  render_fetch_batch_markdown(...)   逐条「## N. 标题 / 链接 / 正文」
```

计费按每条 fetch 单独算：批量只省时间，不省钱。429 交给既有的逐条错误映射处理。
配置项 `[fetch].max_batch` / `[fetch].max_batch_concurrency`。

### 调用内并发：把串行的 await 摊开

`fetch_top` 是「一次搜索 + 前 N 条正文」的组合：`runtime.search()` 拿到列表后，
对前 N 条 URL 调 `fetch_many`，一次返回「列表 + 正文」，省掉模型侧的多轮工具往返。
上限 5，默认 0（关闭）；配置项 `[search].fetch_top`，工具参数优先。

`components/render.py::prepare_search` 的正文压缩也改成了并发：

```text
旧：for item in results: await shrink_text(item.content)   # 3 条 = 3 次串行 LLM 调用
新：先判定哪些条目需要压缩（正文非空 + 在 search_item_limit 之内）
    → asyncio.gather(shrink(item) for item in needs)
    → 其余照旧截断；顺序与 used 计数语义保持不变
```

批量渲染（`render_fetch_batch_markdown` / `render_fetch_batch_text`）只做顺序拼接：正文抓取已经并行，
渲染层不再额外开并发。开启小模型压缩时，每条正文仍会各自走一次 `prepare_fetch`（可能包含一次 LLM 调用），
这一步刻意保持串行，避免一次批量抓取同时打给压缩模型一堆请求。抓取并发度受信号量约束（默认 5），
不会打爆连接池（8）或触发 429。

## 自然语言触发

Tool 路径依赖模型自己决定是否调用（`tool_choice=auto`），小模型经常不调。触发器把「是否检索」从模型手里拿回来：

```text
适配器 → receiver.receive_envelope
            │
            ├─ 发布 BEFORE_MESSAGE_RECEIVED
            │     └─ DeepSeekNaturalTrigger.execute
            │          1. 取文本段（兼容单段 dict / 段列表 / {"text": ...}）
            │          2. 开关、群聊私聊、命令前缀、意图正则
            │          3. 冷却（平台+群号/用户号）与每日自动触发上限
            │          4. clean_query：剥掉「帮我搜一下」等口语前缀
            │          5. runtime.search(..., timeout=20s)  ← 失败/超时/无结果 → PASS
            │          6. 就地把结果追加进文本段，返回 SUCCESS
            ▼
       MessageConverter → 正常 chatter 流程（LLM 已能看到结果）
```

设计取舍：

- **为什么注入消息而不是另发一条**：另发消息要自己管发送与上下文同步，还会让用户看到两段；注入进本轮消息最省事，模型也一定看得到。代价是注入内容会进入会话历史，因此提供 `max_inject_chars` / `count` 控制体积，并可用 `[trigger].enabled` 整体关闭。
- **为什么用 `timeout = 0`**：事件总线默认 30s 超时，取消耗时后我们会丢掉已经发出的请求；由插件用 `asyncio.wait_for` 自己控制更可控（默认 20s < 30s）。
- **为什么异常一律 PASS**：触发器是增强，不是必经链路；任何失败都不能让用户的消息发不出去。
- **为什么冷却按「平台+群号/用户号」**：群聊里多个用户连续提问是常态，按流冷却能避免一次话题引发连续计费，同时不误伤别的群。

## 正文压缩

长网页直接塞进上下文既贵又容易超限。压缩是**可选增强**，默认关闭：

```text
fetch / search(need_content)
        │
        ▼
core/compressor.py  ContentCompressor.shrink()
   ├─ 关闭            → 按 fallback_chars 截断（旧行为）
   ├─ 短于 target      → 原样返回
   ├─ 超长输入         → 头 65% + 尾 35% 采样
   ├─ 调用小模型       → components/llm_bridge.py → llm_api → model_tasks/utils_small
   └─ 失败 / 超时 / 空 → 回退截断，并带 reason
        │
        ▼
components/render.py  统一渲染（三条路径共用）
   └─ 正文已定稿 → format_*(max_content_chars=0) → 追加「已压缩 / 已截断」脚注
```

几个刻意的决定：

- **失败必须降级**：压缩是增强，不是必经链路。超时、模型 401、返回空串都只是「少一段压缩」，不能让整次抓取失败。
- **输入要设上限**：网页长度不可控，`max_input_chars` 是全链路唯一需要防的地方（上下文与费用）。
- **压缩结果要缓存**：抓取结果本身有缓存，如果压缩不缓存，同一条缓存命中每次都重新调模型，等于缓存白做。
- **模型只跑纯文本补全**：不挂工具、不带聊天上下文，避免被对话状态影响，也避免误触发工具调用。

## 错误映射

| 上游状态 | 异常 | 是否重试 | 面向用户的建议 |
| --- | --- | --- | --- |
| 400 | `BadRequestError` | 否 | 检查 query 长度与 count 范围 |
| 401 | `AuthError` | 否 | 检查 `[api].api_key` |
| 402 | `InsufficientBalanceError` | 否 | 余额不足，充值 |
| 403 | `ForbiddenError` | 否 | 换来源站点 |
| 404 | `NotFoundError` | 否 | 纯 JS 页面或已删除 |
| 429 | `RateLimitedError` | 是（遵循 Retry-After） | 稍后再试 |
| 5xx / 504 | `UpstreamError` | 是（指数退避） | 上游异常 |
| 网络 / 超时 | `TransportError` | 是 | 检查出网与代理 |

## 计费口径

- 只有「打到上游且拿到结果」的请求才写 `usage.record_success()`。
- 空结果写 `record_empty()`，失败写 `record_failure()`，两者都不计入费用与配额。
- 缓存命中写 `record_cache_hit()`，同样不计费。
- 费用 = 成功请求数 × `price_per_1000 / 1000`，展示时保留 3 位小数。

## 测试策略

- `tests/framework_stub.py` 提供框架替身：配置字段、组件基类、日志、存储、权限、schema 生成。
- `tests/helpers.py` 提供假客户端 / 假存储 / 假时钟，用 `Runtime` 的依赖注入挂进去。
- 覆盖面：query 解析、缓存（含 `ori_question` 是否进键）、用量、客户端（含 401/402/404/429/5xx/网络/坏 JSON/端点白名单/keepalive）、TLS 上下文复用、加载期预热、批量抓取（并发峰值 / 失败隔离 / 去重保序 / 上限截断）、压缩并发、运行时编排、格式化、Tool、Command、插件生命周期、manifest 与代码一致性。
- 全部离线：不读真实 Key、不联网、不依赖本机 bot 安装。

## 本地联调

```bash
# 1. 装进 bot（按正确的目录名）
python scripts/install_local.py --plugins-dir "<bot>/plugins" --force

# 2. 完全重启 bot（插件与配置都不热加载）

# 3. 私聊 bot 发 /ds test 验证连通性（消耗 1 次请求）
```

若只想验证代码在真实框架下能否导入与注册，而不启动 bot：

```python
import sys, importlib
sys.path.insert(0, "plugins")                # bot 的 plugins 目录
sys.path.insert(0, "<bot 根目录>")
mod = importlib.import_module("deepseek_websearch.plugin")
cfg = importlib.import_module("deepseek_websearch.config").DeepSeekWebSearchConfig()
print(mod.DeepSeekWebSearchPlugin(cfg).get_components())
```

## 扩展点

- 要加新的过滤维度（例如按文件类型）：改 `core/query.py` 的 `build_query` 与 `components/tools.py` 的参数，加测试。
- 要做更省钱的缓存（跨会话持久化）：把 `TTLCache` 换成落盘实现，`Runtime` 的接口不用动。
- 要接入别的搜索后端：实现与 `DeepSeekClient` 相同的 `search` / `fetch` / `fetch_many` / `aclose`，注入 `Runtime(client=...)` 即可。
- 要换证书来源（例如改用系统证书库）：只改 `core/tls.py` 的 `_build_context()`，调用方不用动。
