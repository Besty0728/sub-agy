---
description: 派发一个或多个计划给 sub-agy 异步执行
argument-hint: "<plan.md|raw task> [--model ...] [--effort ...] [--timeout ...] [--no-worktree]"
allowed-tools: Bash, Read, Write
---

把计划派发给 sub-agy 执行。

$ARGUMENTS 的解析规则：
1. 若第一个 token 是已存在的 `.md` 文件，则把所有存在的 `.md` token 视为计划文件，剩余 token 作为透传给 `sub-agy run` 的旗标。
2. 否则把整个 $ARGUMENTS 视为原始任务文本，先 Write 到 `.subagy/inbox/<时间戳>.md`（frontmatter 留空模板，方便后续补 scope/acceptance/constraints），再执行派发。

### 模型与思考强度抉择

主 agent 在派发每个计划前，应按任务复杂度自主抉择 `--effort`（以及必要时的 `--model` / `--timeout`）：

- **默认值**：model = `gemini-3.7-flash`（flash 系列，配额友好），effort = `medium`。不传旗标即为此默认值，无需显式写。
- **effort 自主抉择**（主 agent 每次派发前主动判断，在汇报卡片中注明所选档位与理由）：
  | 任务特征 | effort |
  |---|---|
  | 单文件小改、文案/文档、机械重命名、简单脚本 | `low` |
  | 常规多文件特性、带测试的修改（默认档） | `medium` |
  | 跨模块架构调整、复杂调试、需要长链路推理的计划 | `high` |
- **model 抉择**：默认不换（`gemini-3.7-flash`）。仅当用户明确要求时更换；可用 slug 可通过 `agy models` 查询（flash 系列优先）。
- **timeout 抉择**：按任务规模可通过 `--timeout` 透传（默认 30m）。

对每一个计划文件：
- 读取项目根 `${CLAUDE_PROJECT_DIR:-$PWD}`。
- 执行：`bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" run --plan <file> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}" [透传旗标]`。
- 收集每个作业的 job_id、branch、worktree、查询命令。

批量派发时逐个 run，任意一个失败也不中断后续派发，但在最终汇总中标记失败原因。

输出格式：每个作业一张紧凑 markdown 卡片，包含 `档位: model=<slug> effort=<low|medium|high>`（所选值，默认也要写明）与选择理由（完成后 tokens 消耗与用时见 harvest）。

**watcher 主动通知**：派发汇报完所有作业卡片后，用 Bash run_in_background 启动后台 watcher：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" watch <所有新 job id> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"
```

明确告诉主 agent：**watcher 已挂，完成时我会自动开始审查**。watcher 退出即表示作业全部进入终态，届时主 agent 会收到系统通知并按 `harvest.md` 的流程与硬性规则自动进入审查——不要等用户喊。提示语改为：

> 作业已派发，watcher 已挂，完成时我会自动开始审查。

`dispatch` 自身仍只负责把作业放进队列并启动 detached supervisor，不原地轮询。
