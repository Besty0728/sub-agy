---
name: subagy-harvest
description: |
  This skill should be used when sub-agy jobs have reached a terminal state and need to be harvested, reviewed, and accepted/rejected.
  Triggers on: "收割", "harvest", "验收", "acceptance", "review results", "检查结果", "作业结果", "sub-agy 结果", "job status done error".
  It runs status --all, fetches result.json, inspects diffs, summarizes, and either feedbacks or recommends merge.
---

# sub-agy harvest（收割验收）

收割 `sub-agy` 已终态作业的结果，做结构化验收，并决定打回修复或建议合并。

> 命令假设 `sub-agy` 已安装（推荐 `uv tool install --editable /path/to/sub-agy`）。若未安装，可 `export SUB_AGY_HOME=/path/to/sub-agy` 后使用 `uv run --project "$SUB_AGY_HOME" sub-agy ...`。

## 触发条件

- `watch` 已返回，作业进入终态。
- 用户说“收割”“harvest”“验收”“查看结果”“sub-agy 结果”。
- 由 `dispatch` 流程在 `watch` 完成后自动调用。

## 流程

### 1. 找出待收割作业

若无参数：

```bash
sub-agy status --all --json --cwd "<项目根>"
```

筛选 `state` 为 `done` 或 `error` 的作业。若用户指定了 job-id，则只收割这些 id（仍建议先 status 确认状态）。

### 2. 获取验收单

对每个目标作业：

```bash
sub-agy result <job-id> --cwd "<项目根>"
```

`result` 未完成时会 exit 4；此时应让用户等待或先 `watch`。

### 3. 查看变更

从 `result.json` 读取：

- `files_changed_git`：变更文件列表
- `diff_stat`：`git diff --stat` 文本
- `structured_output`：包含 `summary`、`files_changed`、`tests_ran`、`tests_passed`、`acceptance_met`、`blockers`、`followups`

必要时查看 worktree 中的具体文件或执行 `git diff`。

### 4. 输出汇总表

| job_id | contract_ok | tests_passed | 验收结论 | 建议动作 |
|--------|-------------|--------------|----------|----------|
| j-...  | true        | true         | 通过     | `git merge agy/j-...` |
| j-...  | false       | true         | 不通过   | `sub-agy feedback j-... "..."` |

## 动作规则（铁律）

- **验收不通过**（`contract_ok` 为 false，或 `tests_passed` 为 false，或 diff/文件不符合预期）→ **自动**执行：
  ```bash
  sub-agy feedback <job-id> "<具体、可操作的修复要求>"
  ```
  这是设计目的：打回自动化。
- **验收通过** → **只**给出合并命令建议（例如 `git merge agy/<job-id>` 或 cherry-pick），**绝不自行合并、提交或清理**。合并与 cleanup 永远由用户手动决定。

## 禁止事项

- **不要对 `running` 或 `queued` 作业执行 feedback**。若目标仍在运行，告诉用户稍后再收割。
- **不要自动 merge / commit / cleanup / 删除 worktree**。
- `feedback` 只对 `done`/`error` 状态有效。

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy status --all --json --cwd ./my-project
uv run --project "$SUB_AGY_HOME" sub-agy result <job-id> --cwd ./my-project
uv run --project "$SUB_AGY_HOME" sub-agy feedback <job-id> "<修复要求>" --cwd ./my-project
```
