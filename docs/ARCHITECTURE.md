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
- 覆盖面：query 解析、缓存、用量、客户端（含 401/402/404/429/5xx/网络/坏 JSON/端点白名单）、运行时编排、格式化、Tool、Command、插件生命周期、manifest 与代码一致性。
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
- 要接入别的搜索后端：实现与 `DeepSeekClient` 相同的 `search` / `fetch` / `aclose`，注入 `Runtime(client=...)` 即可。
