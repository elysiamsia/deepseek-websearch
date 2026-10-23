# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
