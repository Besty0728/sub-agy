---
description: 派发一个或多个计划给 sub-agy 异步执行
argument-hint: "<plan.md|raw task> [--model ...] [--effort ...] [--timeout ...] [--auto-approve] [--no-worktree]"
allowed-tools: Bash, Read, Write
---

把计划派发给 sub-agy 执行。

$ARGUMENTS 的解析规则：
1. 若第一个 token 是已存在的 `.md` 文件，则把所有存在的 `.md` token 视为计划文件，剩余 token 作为透传给 `sub-agy run` 的旗标。
2. 否则把整个 $ARGUMENTS 视为原始任务文本，先 Write 到 `.subagy/inbox/<时间戳>.md`（frontmatter 留空模板，方便后续补 scope/acceptance/constraints），再执行派发。

对每一个计划文件：
- 读取项目根 `${CLAUDE_PROJECT_DIR:-$PWD}`。
- 执行：`bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" run --plan <file> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}" [透传旗标]`。
- 收集每个作业的 job_id、branch、worktree、查询命令。

批量派发时逐个 run，任意一个失败也不中断后续派发，但在最终汇总中标记失败原因。

输出格式：每个作业一张紧凑 markdown 卡片。

**watcher 主动通知**：派发汇报完所有作业卡片后，用 Bash run_in_background 启动后台 watcher：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" watch <所有新 job id> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"
```

明确告诉主 agent：**watcher 已挂，完成时我会自动开始审查**。watcher 退出即表示作业全部进入终态，届时主 agent 会收到系统通知并按 `harvest.md` 的流程与硬性规则自动进入审查——不要等用户喊。提示语改为：

> 作业已派发，watcher 已挂，完成时我会自动开始审查。

`dispatch` 自身仍只负责把作业放进队列并启动 detached supervisor，不原地轮询。
