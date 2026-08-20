---
name: subagy-dispatch
description: |
  This skill should be used when the user wants to dispatch / delegate / send a coding task to the sub-agy backend for asynchronous execution.
  Triggers on: "派发", "dispatch", "delegate", "派给 agy", "antigravity", "agy", "把任务交给 agy", "后台执行代码任务", "异步执行计划".
  It writes raw task text to the inbox, runs `sub-agy run`, then blocks with `sub-agy watch` and transitions to harvest.
---

# sub-agy dispatch（派发计划）

把用户给出的代码执行任务派发给 `sub-agy`，由本机 `agy` 在独立 git worktree 里异步执行。

> 命令假设 `sub-agy` 已安装（推荐 `uv tool install --editable /path/to/sub-agy`）。若未安装，可 `export SUB_AGY_HOME=/path/to/sub-agy` 后使用 `uv run --project "$SUB_AGY_HOME" sub-agy ...`。

## 触发条件

- 用户说“派发”“dispatch”“delegate”“派给 agy”“让 agy 做”“后台执行这个计划”。
- 用户给出计划文件路径，或直接给出一段任务描述。

## 输入解析

1. 若用户提供的是已存在的 `.md` 文件路径，把它当作计划文件。
2. 否则把用户输入的**原始任务文本**先写成计划文件：
   ```bash
   mkdir -p "<project>/.subagy/inbox"
   cat > "<project>/.subagy/inbox/<timestamp>.md" <<'EOF'
   ---
   scope: []
   acceptance: []
   constraints: []
   ---
   <原始任务文本>
   EOF
   ```
   - `<timestamp>` 用 `date +%Y%m%d-%H%M%S` 或 ISO-8601 格式。
   - frontmatter 中的 `scope`/`acceptance`/`constraints` 先留空列表占位，方便后续补全。

## 模型与思考强度抉择

派发每个计划前，主 agent 必须按任务复杂度自主抉择 `--effort`（以及必要时的 `--model` / `--timeout`）：

- **默认值**：model = `gemini-3.7-flash`（flash 系列，配额友好），effort = `medium`。不传旗标即为此默认值，无需显式传参。
- **effort 自主抉择**（每次派发前主动判断，在汇报卡片中注明所选档位与理由）：
  | 任务特征 | effort |
  |---|---|
  | 单文件小改、文案/文档、机械重命名、简单脚本 | `low` |
  | 常规多文件特性、带测试的修改（默认档） | `medium` |
  | 跨模块架构调整、复杂调试、需要长链路推理的计划 | `high` |
- **model 抉择**：默认不更换（`gemini-3.7-flash`）。仅当用户明确要求时更换；可用 slug 可通过 `agy models` 查询（flash 系列优先）。
- **timeout 抉择**：按任务规模通过 `--timeout` 透传（默认 30m）。

## 派发单个计划

```bash
sub-agy run --plan "<plan.md>" --cwd "<project根>" [--model gemini-3.7-flash] [--effort low|medium|high] [--timeout 30m] [--no-worktree]
```

- `--model`、`--effort`、`--timeout`、`--no-worktree` 与 `src/sub_agy/cli.py` 完全一致。
- `run` 默认**立即返回** `job_id`，作业在 detached supervisor 里继续执行。

## 批量派发

多个计划文件时**逐个 run**，收集所有 `job_id` 后统一进入下一步。

## 阻塞等待（Codex 桌面端模式）

Codex 没有 Claude Code 的 Bash `run_in_background` 主动唤醒机制，因此派发后**必须原地阻塞等待**。但为了尽早收割完成的作业，使用**增量收割策略**：

**按派发顺序逐 job 单独 watch**（而非把多个 id 塞进一个 `watch` 命令）：

```bash
sub-agy watch j-001 --strict --cwd "<项目根>" --timeout <该作业的等待上限>
# j-001 终态后立即对它走 harvest 规则、feedback 或 merge
# 然后 watch 下一个
sub-agy watch j-002 --strict --cwd "<项目根>" --timeout <该作业的等待上限>
# 依次...
```

- `watch --strict`：全部终态且仅含 `done` → exit 0；任一 `error`/`cancelled`/`interrupted` → exit 1。
- 超时 exit 124，但仍会打印当前状态；此时可重新执行原命令 **续等**（幂等）。
- 单个 job 终态后，`watch` 输出 JSON 数组（单元素）：`job_id, state, round, agy_status, summary, contract_ok, tests_passed, elapsed_seconds, tokens, diff_stat, result_path, events_path, worktree, branch`。
- **超时建议值**：`该作业的 timeout × (queue_position 或 1) + 30m 余量`（考虑排队等候）。
- 退出码：终态 → 0（strict 下仅 done）或 1（任一非 done 终态）；job 不存在 → 3；超时 → 124。

## watch 返回后的处理

`watch` 返回后，立即按输出中的 `state`/`contract_ok`/`tests_passed` 进入**验收流程**（harvest 技能规则）：

- 验收不通过 → 自动 `sub-agy feedback <id> "<具体修复要求>"` 打回（仅 `done`/`error` 状态）。若判断上一轮失败主要由于思考强度不足（需升级 effort），建议以更高 `--effort` 重新 `run` 新作业，因为 `feedback` 会复用原作业的 `meta.json` 档位。
- 验收通过 → 只给出 `git merge agy/<id>` 建议，**绝不自行合并/提交/cleanup**。
- 若状态为 `cancelled`/`interrupted`，向用户说明并等待指示。

## 输出示例

派发后向用户汇报（作业卡片中注明档位、理由，以及完成后 tokens/用时见 harvest）：

> 已派发作业 `j-20260820-120000-ab12`（档位: model=gemini-3.7-flash effort=medium，常规多文件修改），分支 `agy/j-20260820-120000-ab12`，正在 watch 阻塞等待完成（完成后 tokens/用时见 harvest 汇总）...

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy run --plan task.md --cwd ./my-project
```
