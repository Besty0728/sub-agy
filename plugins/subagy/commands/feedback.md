---
description: 对已完成或出错的 sub-agy 作业提交反馈，启动下一轮修复
argument-hint: "<job-id> <feedback message>"
allowed-tools: Bash
---

对 sub-agy 作业提交反馈。

- 第一个 token 必须是 job-id。
- 剩余部分为反馈文本，应具体、可操作。
- 执行：`bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" feedback <job-id> "<feedback message>" --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"`。

反馈只会被接受当作业状态为 `done` 或 `error` 且存在 `conversation_id`。反馈后作业会重新进入 `queued` 并开始新一轮执行。返回新 round 编号。

**档位与打回判断**：
`feedback` 本身不修改 `model` 与 `effort`（新 round 自动复用作业的 `meta.json` 档位）。主 agent 在打回前应判断上一轮失败是否因思考不足：
- 若仅为细节偏差、小测试失败或局部遗漏，直接调用 `feedback` 进行下一轮修复；
- 若判断失败原因是思考强度不足（例如复杂逻辑推导错误、架构死锁），需要升级 effort 档位，建议不要使用 `feedback`，而是修改计划后用更高的 `--effort`（如 `high`）重新 `dispatch` / `run` 新作业。

不要对 `running` 或 `queued` 作业调用此命令。
