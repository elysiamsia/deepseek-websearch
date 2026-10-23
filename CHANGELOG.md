# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
