# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.4.0] - 2026-11-13

这一版只做一件事：把「等」变短。检索语义、结果格式、计费口径都没有变，
变的是一次搜索要花多久——尤其是冷启动的那一次。

### 修复

- **401 的真正原因**：插件配置 `[api].api_key` 里被填了一个 32 位、`fsf3` 开头的值，
  它不是 DeepSeek 官方格式（官方 Key 以 `sk-` 开头），发到 `api.deepseek.com` 必然 401；
  而同一台机器的 `config/model.toml` 里其实躺着一个可用的官方 Key。
- 解析 Key 时新增**格式自检**：目标为官方端点时，不是 `sk-` 开头的候选会被跳过并继续回退
  （配置 → 环境变量 → model.toml），每一步跳过都会在日志里写明原因。
- 401 报错现在会附带**实际使用的 Key 来源与掩码**（例如 `config，fsf3****DmqD`），
  一眼就能分清是配置里的 Key 失效，还是回退到的 model.toml Key 失效。
- `/ds test` 的「Key 来源」一行也带掩码，便于核对。
- 非官方端点（自建中转）不做格式校验，避免误伤任意格式的中转 Key。

上面这一节原本计划作为 1.3.3 单独发布，后来决定不再单独发版，直接并入 1.4.0。

### 提速

- **复用 TLS 上下文**（`[api].reuse_ssl_context`，默认开）：新增 `core/tls.py`，
  把 certifi 根证书用 `cadata` 一次性解析成进程内共享的 `SSLContext`。
  `httpx` 默认按文件路径逐张加载 120 个根证书要 850~890 ms，现在整个进程只付一次、约 20 ms；
  构建失败会自动回退 `httpx` 默认行为，关掉开关也一样回退。
- **加载期预热**（`[plugin].prewarm`，默认开）：插件加载后在后台把 TLS 上下文与
  `httpx.AsyncClient` 建好，首次搜索不再现付这份准备成本。
  刻意不做网络预热——预热请求会真实计费，还会污染用量统计。
- **延长 keepalive**（`[api].keepalive_seconds`，默认 120 秒）：群聊里往往隔几分钟才问一次，
  5 秒的连接池等于每次都重新握手。填 0 回到 `httpx` 默认的 5 秒。
- **缓存键不再包含 ori_question**（`[cache].key_includes_ori_question`，默认关）：
  同一 query 换个问法也能命中缓存，不再白付一次请求；`ori_question` 照常发给上游影响排序。
  想按字面问法各算各的，把开关打开即可。
- **`deepseek_fetch` 支持 `urls` 批量并行**：`asyncio.gather` + 信号量，
  单条失败不影响其它条；去重、保序，超过 `[fetch].max_batch`（默认 5）时截断并在结果里说明。
  3 个链接从 384 ms 降到约 189 ms。
- **`deepseek_search` 新增 `fetch_top`**（`[search].fetch_top`，默认 0）：
  一次调用返回「结果列表 + 前 N 条正文」，省掉模型「先看列表、再逐条 fetch」的多轮往返，上限 5。
- **单次调用内部并发化**：搜索结果正文的压缩改为并发（`prepare_search`），
  开小模型时 3 条不再串行等 3 次 LLM；批量抓取则用信号量把多个 fetch 摊开（见上）。
  批量渲染只做顺序拼接，不再额外加一层并发。
- **自然语言触发的注入更短**：默认条数与注入上限收紧，见下一节。

### 默认值变化

- `[trigger].count`：5 → **3**。3 条足够回答大多数「帮我搜一下」，注入越短 LLM 首字越快。
- `[trigger].max_inject_chars`：1800 → **800**。理由同上；想要更完整的回答，
  这两个值都可以在配置里调回 5 / 1800。
- 新增 7 个配置项：`reuse_ssl_context`、`keepalive_seconds`、`prewarm`、
  `key_includes_ori_question`、`max_batch`、`max_batch_concurrency`、`fetch_top`。
  默认值就是推荐的提速组合；老配置文件无需迁移，缺省字段自动取默认值。

### 测试

- 基线 236 条离线测试全绿；本版为每项改动补测试，条数只增不减。
- 新增 `scripts/bench_live.py`：默认只打印测量项目与命令，加 `--live` 才真调 API；
  Key 只从环境变量或 model.toml 读取、只打印掩码，不写入任何文件。
- 真机验收（原型）：首次搜索 1681 ms → 464 ms（目标 ≤700 ms）；3 URL 抓取 384 → 189 ms。

## [1.3.2] - 2026-10-24

### 变更

- README 再次调整：只借用爱莉希雅的**表达风格**（语气、反问、比喻、温柔的提醒），
  叙述者仍是文档本身——不再使用第一人称扮演，也不再自称角色、不再署名。

## [1.3.1] - 2026-10-24

### 变更

- 按「爱莉希雅」的第一人称文风重写 README（技术内容与配置表保持完整，语气换成她的）。
- manifest 补充 `homepage` / `repository` / `license`，便于插件市场展示。
- 上架 Neo-MoFox 插件市场（首次注册）。

### 说明

- 本次只改文档与元数据，代码行为与 1.3.0 完全一致。
- 227 条离线测试、`mpdt plugin check` 均通过。

## [1.3.0] - 2026-10-24

### 新增

- **可选的正文压缩**（`[compress]`，默认关闭）：
  - 打开后抓到的长正文不再受 `max_chars` / `max_content_chars` 限制，
    超过 `target_chars` 就交给小模型压缩后再交给 LLM。
  - 默认模型 `utils_small`（先按 model_tasks 任务名解析，失败再按 models 模型名），可自定义。
  - 超长输入按「前 65% + 后 35%」采样，中间标注省略字符数。
  - 压缩失败 / 超时 / 空返回一律回退截断，结果标注「压缩未生效，已截断到 N 字符」。
  - 压缩结果按正文哈希缓存，重复渲染不会重复调模型。
  - 可分别控制是否作用于网页抓取（`apply_to_fetch`）与搜索结果正文
    （`apply_to_search_content`），并限制单次搜索压缩条数（`search_item_limit`）。
- 新增配置项 `[fetch].command_max_chars`（默认 800），修掉 `/ds fetch` 展示上限写死的问题。

### 变更

- 新增统一渲染层 `components/render.py`：LLM 工具、`/ds` 命令、自然语言注入三条路径共用同一套
  「压缩 / 截断 → 格式化」逻辑，配置语义不再分叉。
- `components/llm_bridge.py`：把 `llm_api` 包成一次纯文本补全，供压缩器注入使用。

### 测试

- 新增 27 条测试（压缩器、渲染层、Runtime 压缩缓存），总计 227 条离线测试。
- 真机验证：`utils_small` 把 4320 字正文压到 210 字，渲染带压缩脚注。

## [1.2.0] - 2026-10-24

### 修复

- **移除与本机绑定的 Key 回退**：不再读取 `~/.claude/deepseek-websearch.json`。
  那是开发机上的私有路径，别人部署 bot 时根本不存在，属于环境泄漏。
- 第三级回退改为**本实例的 `config/model.toml`**：自动从 `[[api_providers]]` 里挑选
  `base_url` 指向 `api.deepseek.com` 的条目并复用其 `api_key`，
  只要 bot 的对话模型用的是 DeepSeek 官方端点，装完插件即可直接搜索。

### 新增

- 配置项 `[api].model_config_path`（默认 `config/model.toml`）与
  `[api].key_provider`（强制指定 provider，留空自动挑官方端点）。
- 支持 `api_key` 写成**数组**（多 Key 轮换写法）的 model.toml：取第一个非空项，避免把列表 repr 当作 Key 发送。
- 新增 18 条 Key 解析测试，其中一条回归守卫断言解析过程**绝不访问用户主目录**。
- 总计 200 条离线测试。

### 变更

- 解析链固定为：`[api].api_key` → `DEEPSEEK_API_KEY` → 本实例 `model.toml` 的官方 provider。
- `MissingAPIKeyError` 的提示同步为新链路。

## [1.1.0] - 2026-10-23

### 新增

- **自然语言触发**（EventHandler，订阅 `BEFORE_MESSAGE_RECEIVED`）：消息命中意图正则时，插件自己发起一次检索，把结果作为「系统注入」追加到本轮消息，不再依赖模型自觉调用工具。
  - 三道闸门：意图正则、同一聊天流冷却、每日自动触发上限（默认 200 次/天）。
  - 群聊 / 私聊独立开关；命令前缀（`/`、`!`）消息自动跳过。
  - 超时、检索失败、无结果一律原样放行，绝不阻断聊天。
  - 注入内容带明确边界标记 + 外部数据不可信提示。
- 配置新增 `[trigger]` 段（16 个字段，含可自定义的 `patterns`）。
- Runtime 新增 `now()` / `monotonic()` 访问器，供上层做按日计数与冷却（可注入测试时钟）。

### 变更

- `manifest.json` 增加 `event_handler` 组件，版本升到 1.1.0。
- `core/formatting.py` 的截断函数改为公开的 `truncate_text`，供触发器复用。

### 测试

- 新增 26 条触发器测试（意图匹配、注入、冷却、每日上限、群聊/私聊开关、超时与失败放行、段列表形态、截断、坏正则容错）。
- 总计 182 条单元测试，全部离线。

## [1.0.0] - 2026-10-23

首个版本。

### 新增

- 两个 LLM 工具：`deepseek_search`（官方搜索，支持 `site` / `exclude_site` / `lang` / `after` / `before` 结构化过滤）与 `deepseek_fetch`（网页转 Markdown）。
- `/ds` 聊天命令：直接搜索、抓取、查看用量、清理缓存、自检、清零统计。
- 跨插件服务 `deepseek_websearch:service:websearch`。
- TTL + LRU 结果缓存，缓存命中不重复计费。
- 用量与费用统计（按成功请求计费，空结果与失败不计费），可持久化到 JSON 存储。
- 每日请求配额与「外部数据不可信」安全提示。
- 429 / 5xx / 网络错误指数退避重试，401 / 402 / 403 / 404 即时失败并附中文修复建议。
- 抓取 URL 的 SSRF 防护（默认拦截本机 / 内网地址）。
- 300 行以内的框架替身 + 156 个不联网单元测试。
- `.mfp` 打包脚本、本地安装脚本与 GitHub Actions 流水线。
