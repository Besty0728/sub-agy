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

不要对 `running` 或 `queued` 作业调用此命令。
