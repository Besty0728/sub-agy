---
description: 收割 sub-agy 已完成作业的结果并验收。触发词：收割、harvest、验收、审查、检查、审批
argument-hint: "[job-id ...]"
allowed-tools: Bash, Read
---

收割与验收 sub-agy 作业。

触发方式：
- 可由 watcher 完成通知自动触发（`dispatch` 为每个 job 各挂的后台 watcher 退出时）。**此时只收割该 watcher 盯的那一个 job id**，不要顺手把其他还在跑的作业拉进来。
- 也可由用户手动调用 `/subagy:harvest [job-id ...]`。

流程：
1. 若 $ARGUMENTS 为空，执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" status --all --json --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"`，找出 `state` 为 `done` 或 `error` 的作业。
2. 若用户指定了一个或多个 job-id，则只收割这些作业（仍建议先 status 确认它们已完成）。
3. 对每个目标作业：
   - 执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" result <job-id> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"` 拿到结构化验收单。
   - 用 Read 查看 result.json 中的 `files_changed_git`、`diff_stat`、`usage`，必要时查看 worktree 中的具体文件或执行 `git diff`。
4. 输出汇总表，列：job_id / contract_ok / tests_passed / tokens / 用时 / 验收结论 / 建议动作。（tokens 取 result.json `usage.total_tokens`，按 k/M 人性化显示；用时取 `elapsed_seconds`，按 m:ss 人性化显示）。

**动作规则（硬性）**：
- 验收不通过（`contract_ok` 为 false，或 `tests_passed` 为 false，或 diff/文件不符合预期）→ **自动**执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" feedback <job-id> "<具体、可操作的修复要求>"` 打回。打回后**立即为该作业重挂一个单 id 后台 watcher**——发起一次 Bash `run_in_background` 调用（不要用 shell `&`），命令与超时规则同 `dispatch.md` 的 watcher 节：
  ```bash
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" watch <job-id> --strict --cwd "${CLAUDE_PROJECT_DIR:-$PWD}" --timeout <见 dispatch.md>
  ```
  - 重挂 watcher 确保第二轮修复完成时有人监听，否则自动回路断裂。
  - 此后该作业的后续完成再次进入 harvest 流程。
- 验收通过 → **建议合并**：给出命令建议（例如 `git merge agy/<job-id>`）后，询问用户是否已合并。**当用户确认已合并，或执行 `git branch --merged` 显示 `agy/<job-id>` 已并入当前分支时**，自动执行：
  ```bash
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" cleanup <job-id> --delete-branch --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"
  ```
  - 确认已合并后 cleanup 直接执行，不再征求用户同意；未确认合并前绝不清理。

**注意**：不要对 `running` 或 `queued` 作业执行 feedback。若目标仍在运行或仍在排队等槽位，告诉用户稍后再收割。
