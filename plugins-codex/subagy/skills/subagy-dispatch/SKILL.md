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

## 派发单个计划

```bash
sub-agy run --plan "<plan.md>" --cwd "<project根>" [--model gemini-3.7-flash] [--effort low|medium|high] [--timeout 30m] [--auto-approve] [--no-worktree]
```

- `--model`、`--effort`、`--timeout`、`--auto-approve`、`--no-worktree` 与 `src/sub_agy/cli.py` 完全一致。
- `run` 默认**立即返回** `job_id`，作业在 detached supervisor 里继续执行。

## 批量派发

多个计划文件时**逐个 run**，收集所有 `job_id` 后统一进入下一步。

## 阻塞等待（Codex 桌面端模式）

Codex 没有 Claude Code 的 Bash `run_in_background` 主动唤醒机制，因此派发后**必须原地阻塞等待**作业进入终态：

```bash
sub-agy watch <job-id> [job-id...] --cwd "<项目根>" --timeout <按任务规模, 默认 30m>
```

- `watch` 会轮询每个作业，直到**全部**进入终态（`done`/`error`/`cancelled`/`interrupted`）。
- 超时 exit 124，但仍会打印当前状态；此时可重新执行 `sub-agy watch <job-id> --cwd <根>` **续等**（幂等）。
- 全部终态后，`watch` 输出 JSON 数组，每个元素包含：`job_id, state, round, agy_status, summary, contract_ok, tests_passed, elapsed_seconds, diff_stat, result_path, events_path, worktree, branch`。
- 退出码：全 `done` → 0；有任何 `error`/`cancelled`/`interrupted` → 1。

## watch 返回后的处理

`watch` 返回后，立即按输出中的 `state`/`contract_ok`/`tests_passed` 进入**验收流程**（harvest 技能规则）：

- 验收不通过 → 自动 `sub-agy feedback <id> "<具体修复要求>"` 打回（仅 `done`/`error` 状态）。
- 验收通过 → 只给出 `git merge agy/<id>` 建议，**绝不自行合并/提交/cleanup**。
- 若状态为 `cancelled`/`interrupted`，向用户说明并等待指示。

## 输出示例

派发后向用户汇报：

> 已派发作业 `j-20260820-120000-ab12`，分支 `agy/j-20260820-120000-ab12`，正在 watch 阻塞等待完成...

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy run --plan task.md --cwd ./my-project
```
