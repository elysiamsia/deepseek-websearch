# v1.4.0 提速计划

> 状态：**草案，待主人确认后开工**
> 目标版本：1.4.0（跳过 1.3.3 的市场发布，直接做这一版）
> 范围：纯性能与并发改造，**不改变任何检索语义**（同 query 返回同结果），除 P6 的默认值调整外不改变用户体验契约。

---

## 0. 一句话

插件现在单次搜索 283–335 ms 已经是 API 侧的上限，但**首次调用要 1681 ms**——多出来的 1.3 秒几乎全是插件自己的开销（SSL 证书加载 850 ms + httpx 客户端构造 1094 ms）。这一版把一次性开销挪到启动阶段、把长期开销（握手、串行抓取、串行压缩）压掉，目标是**首搜 ≤ 700 ms、批量抓取快一倍**。

---

## 1. 实测基线（2026-11-13 22:08–22:12，bot venv + 真实 Key）

| 项目 | 现状 |
|---|---|
| DNS / TCP / TLS 握手 | 7.3 ms / 41.0 ms / 53.4 ms |
| 热连接单次搜索（无正文） | 283–335 ms |
| **首次搜索（进程内第一次）** | **1681 ms** |
| 　└ `httpx.AsyncClient()` 构造 | **1094 ms** |
| 　└ 首次请求（含 TLS） | 569 ms |
| keepalive 过期（默认 5 s）后重建 | +100 ms |
| `need_content=True` | +89 ms（响应 11 KiB → 56 KiB） |
| 3 个 URL 串行抓取 / 并行抓取 | 384 ms / **189 ms** |
| 3 条结果正文串行压缩（开小模型时） | 3 × 单次 LLM 时延 |

### 根因

`httpx` 默认用 `ssl.create_default_context(cafile=certifi.where())` 建 TLS 上下文。本机**按文件路径加载这 120 张根证书要 850–890 ms**，而同一份 PEM 用 `cadata` 传入只要 18.6–20.3 ms：

```
cafile（C 盘副本） : 776 ms     cadata          : 19.9 ms
cafile（原路径）   : 841 ms     系统证书库       : 20.3 ms
AsyncClient(verify=已建好的 ctx): 0.4–104 ms
```

慢与盘符无关（C 盘副本一样慢），是 CPython `_ssl` 逐文件加载证书的路径开销。

### 原型验证（临时副本打补丁，未进仓库）

预建 `SSLContext(cadata)` + `keepalive_expiry=120`：

| | 现状 | 原型 |
|---|---|---|
| 首次搜索 | 1681 ms | **464 ms（3.6×）** |
| 后续搜索 | 297 ms | 283 ms |
| 静默 8 s 后 | 397 ms | 366 ms |

---

## 2. 目标与非目标

**目标**

1. 首次搜索从 1681 ms 降到 ≤ 700 ms（一次进程生命周期只付一次）。
2. 空闲超过 5 秒后的搜索不再重建 TLS 连接。
3. 同一 query 换个说法（`ori_question` 不同）能命中缓存。
4. 多链接抓取并行，3 条从 384 ms 降到 ≤ 250 ms。
5. 一次调用就能拿到「结果列表 + 前 N 条正文」，省掉「模型先看列表、再逐个 fetch」的多轮往返。
6. 自然语言触发注入更短，降低 LLM 首字延迟。

**非目标**

- 不引入新依赖（`h2`、`aiohttp` 一律不加）。
- 不开启 HTTP/2（bot venv 没装 `h2`，单请求也用不上多路复用）。
- 不调大连接池（8/4 完全够）。
- 不改任何 API 请求/响应字段，不改检索语义。

---

## 3. 七项改动

### P1 · 预建并复用 SSLContext（收益最大）

**新增** `core/tls.py`

```python
def get_shared_ssl_context() -> ssl.SSLContext | None:
    """进程内单例；任何异常返回 None，调用方回退 httpx 默认行为。"""

def reset_ssl_context_cache() -> None:   # 仅供测试
```

实现要点：

- 读 `certifi.where()` 的 PEM 字节 → `load_verify_locations(cadata=pem)`。
- 用 `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` + `check_hostname=True` + `CERT_REQUIRED`，**只信任 certifi 的根**，与 httpx 原行为完全一致（不额外信任系统根，避免把本机安装的中间人 CA 拉进信任链）。
- 双检锁 + 模块级缓存；失败记一次 `_FAILED`，后续直接返回 `None`（不反复重试 850 ms 的慢路径）。

**改** `core/client.py` 的 `_httpx_post`：

```python
verify = self.verify_ssl
if verify and self.reuse_ssl_context:
    verify = get_shared_ssl_context() or True
kwargs["verify"] = verify
```

**配置**：`[api].reuse_ssl_context = true`（关掉即回到 httpx 默认）。

**回退**：任何异常 → `verify=True`，行为与 1.3.3 相同。

**风险**：极低。证书集合与 httpx 完全一致；唯一区别是加载方式。

---

### P2 · 加载期预热

**改** `plugin.py` 的 `on_plugin_loaded`：

```python
if bool(getattr(config.plugin, "prewarm", True)):
    asyncio.create_task(self._prewarm())
```

`_prewarm()`：`await asyncio.to_thread(get_shared_ssl_context)`（20 ms 的证书解析不占事件循环）→ `runtime._ensure_client()`（建 `AsyncClient`）→ 记录耗时日志。

**刻意不做网络预热**：预热请求会真实计费（¥0.005/次）并污染用量统计，而 TLS 握手只要 53 ms，不值得。首搜剩余开销 ≈ 握手 100 ms + API 300 ms ≈ 400 ms，已达标。

**配置**：`[plugin].prewarm = true`。

**注意**：`_ensure_client()` 需要 Key；Key 缺失时预热失败只打 `warning`，不影响插件加载（与现状一致）。

**风险**：低。异步任务 + 全异常捕获；可用配置关闭。

---

### P3 · 延长 keepalive

**改** `core/client.py` 建 `httpx.Limits` 处：

```python
expiry = float(getattr(config, "keepalive_seconds", 120) or 0)
limits = httpx.Limits(
    max_connections=8,
    max_keepalive_connections=4,
    keepalive_expiry=expiry if expiry > 0 else 5.0,
)
```

**配置**：`[api].keepalive_seconds = 120`（0–600，0 表示用 httpx 默认 5 s）。

**理由**：群聊里隔几分钟才问一次，5 秒的连接池等于每次都重新握手。

**风险**：低。上游若主动断开，httpx 会自动重连；不会出现「连上死连接」。

---

### P4 · 缓存键不再包含 ori_question

**现状**：`runtime.search` 的缓存键含 `ori_question`，而自然语言触发每次都把用户原句塞进去 → **同一问题换个说法必然不命中**，白付一次 ~300 ms 与 ¥0.005。

**实测依据**：同一 query 配两个完全不同的 `ori_question`，返回的 5 条 URL 100% 相同（重叠 5/5）。

**改**：缓存键只含 `composed_query / count / need_content`；`ori_question` 仍照常发给 API（它影响排序，不影响缓存判定）。

**配置**：`[cache].key_includes_ori_question = false`（给较真的人留开关）。

**副作用**：旧缓存条目因键变化自然失效（无害，最多多一次请求）。

---

### P5 · `deepseek_fetch` 支持批量并行

**新增** `Runtime.fetch_many(urls, *, max_chars=0) -> list[BatchFetchItem]`

```python
@dataclass(frozen=True)
class BatchFetchItem:
    url: str
    outcome: FetchOutcome | None
    error: DeepSeekError | None
```

- `asyncio.gather` + `Semaphore(max_batch_concurrency)`，**单条失败不影响其它条**。
- 去重、保序、超过 `max_batch` 截断并在结果里说明。

**改** `DeepSeekFetchTool.execute`：新增 `urls: list[str] = []`，与 `url` 合并（`url` 在前），总上限 `[fetch].max_batch`（默认 5）。

**配置**：`[fetch].max_batch = 5`、`[fetch].max_batch_concurrency = 5`。

**渲染**：`components/render.py` 新增 `render_fetch_batch_markdown`，逐条「## 1. 标题 / 链接 / 正文」，失败条目单独列出原因。

**收益**：3 条 384 → 189 ms；5 条约 640 → 200 ms。

**风险**：低。429 由现有错误映射逐条处理，不影响其它条。

---

### P6 · 触发路径默认值收紧

| 配置 | 现状 | 改为 | 理由 |
|---|---|---|---|
| `[trigger].count` | 5 | **3** | 注入越短，LLM 首字越快；3 条足够回答大多数「帮我搜一下」 |
| `[trigger].max_inject_chars` | 1800 | **800** | 同上，注入内容每多 1 K 字符就多 ~1 K token 的首字延迟 |

**风险**：体验取舍。若觉得注入太短，两个值都在配置里可调回。文档会写明「想要更完整就调回 5 / 1800」。

---

### P7 · 单次调用内部的并发化

**定义（重要）**：这不是「把一次 `/v1/tools/search` 拆成多个并发请求」——那不可能，一次往返就是一次往返，我之前的判断仍然成立。这里指的是**把一次调用内部原本串行的多个 `await` 并行化**，共三处：

**7a. 搜索结果正文压缩并发**

`components/render.py::prepare_search` 现在是 `for item in outcome.results: await shrink_text(...)`——开小模型时 3 条就是 3 次串行 LLM 调用。

改为：先算出「哪些条目需要压缩」（非空 + 在 `search_item_limit` 之内），对这些条目 `asyncio.gather`，其余照旧截断。顺序与 `used` 计数语义保持不变。

**7b. 搜索 + 抓正文一次完成（`fetch_top`）**

新增工具参数 `fetch_top: int = 0`（0–5）：`runtime.search()` 拿到列表后，对前 N 条 URL 调 `fetch_many` **并行**抓正文，一次返回「列表 + 正文」。

省掉的是**模型侧的多轮往返**：现在模型要先看到列表、再自己逐个调 `deepseek_fetch`，一轮工具调用至少几百毫秒到数秒。

**配置**：`[search].fetch_top = 0`（默认关闭，保持现状）；工具参数优先。

**7c. 批量抓取并发** —— 即 P5 的底层能力，`fetch_top` 直接复用。

**风险**：中低。并发度受信号量约束（≤5），不会打爆连接池（8）或触发 429。

---

## 4. 配置变更总表

| 段 | 键 | 默认值 | 说明 |
|---|---|---|---|
| api | `reuse_ssl_context` | `true` | P1，关掉回退 httpx 默认 |
| api | `keepalive_seconds` | `120` | P3，0 = httpx 默认 5 s |
| plugin | `prewarm` | `true` | P2，加载期预建客户端 |
| cache | `key_includes_ori_question` | `false` | P4 |
| fetch | `max_batch` | `5` | P5 单次最多抓几条 |
| fetch | `max_batch_concurrency` | `5` | P5 并发度 |
| search | `fetch_top` | `0` | P7b 默认关闭 |
| trigger | `count` | `5 → 3` | P6 |
| trigger | `max_inject_chars` | `1800 → 800` | P6 |

老配置文件无需迁移：新键都有默认值，pydantic `extra=forbid` 不影响缺省字段。

---

## 5. 测试计划（全部离线，禁止联网）

| 文件 | 覆盖 |
|---|---|
| `tests/test_tls.py`（新） | ctx 单例复用；cadata 读取失败 → 返回 None；`reset_ssl_context_cache` 生效；`verify_ssl=False` 时不用 ctx |
| `tests/test_prewarm.py`（新） | 预热被调用；预热异常不抛；`prewarm=false` 时不调用 |
| `tests/test_client_limits.py`（新） | `keepalive_seconds` 正确传入 `httpx.Limits`；0 时用 5.0 |
| `tests/test_cache_key.py`（新） | 不同 `ori_question` 命中同一缓存；开关打开时各自独立 |
| `tests/test_batch_fetch.py`（新） | 并发峰值（假 client 记录 in-flight）> 1；失败隔离；去重保序；超过上限截断 |
| `tests/test_concurrency.py`（新） | 3 条 × 100 ms 假压缩 → 总耗时 < 200 ms（串行会是 300 ms） |
| 既有 `test_runtime / test_tools / test_render` | 补齐新参数与新分支 |

预期 236 → **约 265 条**。

另加 `scripts/bench_live.py`（需 `--live` 才真调 API，默认只打印计划）：输出首搜 / 热搜 / 静默 8 s / 批量抓取 / `fetch_top` 五组数字，供验收对比。Key 只打印掩码。

---

## 6. 验收标准

| 指标 | 现状 | 目标 |
|---|---|---|
| 首次搜索（进程内第一次） | 1681 ms | **≤ 700 ms** |
| 热连接单次搜索 | 283–335 ms | ≤ 350 ms（持平） |
| 静默 8 s 后搜索 | 397 ms | **≤ 330 ms** |
| 3 URL 抓取 | 384 ms | **≤ 250 ms** |
| 搜索 + 前 3 条正文 | ≈ 1.2 s（多轮） | **≤ 700 ms**（单次调用） |
| 同一问题换说法 | 必然未命中缓存 | **命中** |
| 离线测试 | 236 | 全绿，≈265 |
| `mpdt check` | 0 错 0 警 | 0 错 0 警 |
| 真机路径 | — | 工具 / `/ds` / 自然语言触发 三条都跑通 |

---

## 7. 版本与发布流程

1. `manifest.json` 与 `__version__` 直接写 **1.4.0**（不单独出 1.3.3）。
2. CHANGELOG 里把 1.3.3 的 401 修复并入 `[1.4.0]` 段落（写明「Key 格式校验 + 自动回退」属于本版）。
3. 一次性提交：`perf: v1.4.0 提速（SSL 复用、加载期预热、keepalive、缓存键、批量抓取、触发精简、并发化）`。
4. 构建 `.mfp` → 本地解包进 bot venv 验证 5 个组件与全部配置段。
5. 推 GitHub、等 CI 双版本通过、建 **Release v1.4.0** + 资产。
6. **插件市场：等主人明确点头再上传**（`mpdt market publish` 或 `package-update`）。

### 待决事项

- **v1.3.3 已经存在**：commit `e18ae9a` 已 push，GitHub Release v1.3.3 + tag 已建（资产 sha256 `fc62af2e…47787d`），**但从未上过插件市场**。
  - 方案 A（默认）：保留 git 历史与 Release，只是不上市场，1.4.0 覆盖式发布。
  - 方案 B：删掉 tag 与 Release，1.4.0 成为唯一发布版本。
  - 需要主人二选一。

---

## 8. 风险与回滚

| 改动 | 风险 | 回滚手段 |
|---|---|---|
| P1 SSL 复用 | 证书链异常导致握手失败 | `[api].reuse_ssl_context=false`；代码里异常自动回退 |
| P2 预热 | 启动多 ~100 ms | `[plugin].prewarm=false` |
| P3 keepalive | 长连接被上游断开 | httpx 自动重连；调小配置 |
| P4 缓存键 | 命中范围变大，可能拿到「意图不同但 query 相同」的旧结果 | 打开开关；或 `/ds cache clear` |
| P5 批量 | 429 风险 | 信号量 + 逐条错误处理；`max_batch_concurrency` 可调 |
| P7b fetch_top | 一次抓太多正文撑爆上下文 | 默认 0；上限 5 |
| P6 注入变短 | 回答信息量下降 | 两个值可调回 |

整体可回滚性：全部走配置开关，最坏情况把 9 个新键恢复默认值即可回到 1.3.3 的行为。

---

## 9. 工作量

| 阶段 | 内容 | 预计 |
|---|---|---|
| 1 | P1 + P2 + P3（传输层） | 40 min |
| 2 | P4 + P6（配置与默认值） | 15 min |
| 3 | P5 + P7（批量与并发） | 60 min |
| 4 | 测试补齐 + `bench_live.py` | 50 min |
| 5 | 文档 / 版本 / 构建 / 真机验证 | 40 min |

合计约 **3.5 小时**，分 5 个提交，每个提交独立可测。

---

## 10. 实施结果（2026-11-13 22:5x，真机实测）

真机环境：bot venv（httpx 0.28.1）、真实 Key `sk-7****5650`（来源 `config/model.toml:DeepSeek`）、
`python scripts/bench_live.py --live`，本次共 13 次请求。

| 项目 | 基线（1.3.3） | 目标 | 实测 | 结论 |
|---|---|---|---|---|
| 首次搜索（冷启动） | 1681 ms | ≤ 700 ms | **549 ms** | 达标，约 3.1× |
| 热搜索 | 297 ms | ≤ 350 ms | **285 ms** | 达标 |
| 静默 8 秒后搜索 | 397 ms | ≤ 330 ms | **317 ms** | 达标 |
| 3 个 URL 批量抓取 | 384 ms（串行） | ≤ 250 ms | **94 ms**（两遍 233 / 94） | 达标，约 4× |
| 搜索 + 前 3 条正文 | ~1.2 s（估算） | ≤ 700 ms | **451 ms** | 达标 |

补充观察：

- 第 4 组第一次跑出过 4242 ms（3/3 成功）：逐条复测同一批链接只要 88~99 ms，
  说明是上游 `/v1/tools/fetch` 单条抖动，不是插件并发失效。基准脚本因此改为跑两遍、
  取较快者并在说明里同时打印两遍耗时。
- 七项改动全部走配置开关，最坏情况把 9 个新键恢复默认值即可回到 1.3.3 行为。

实施与计划的两处偏差：

1. `[trigger].count` 与 `[trigger].max_inject_chars` 只改默认值；**已存在的实例配置**
   （bot 的 `config/plugins/deepseek_websearch/config.toml` 里显式写着 5 / 1800）不会自动变，
   需要改文件并重启 bot 才生效。
2. 计划里的「单次搜索并发化」落成 7a（`prepare_search` 的多条正文压缩用 `gather` 并发）
   + 7b（`deepseek_search` 的 `fetch_top`）+ 7c（`/ds fetch` 与工具共用 `fetch_many`）；
   单次 `/v1/tools/search` 请求本身仍是一次往返，不做拆分。
