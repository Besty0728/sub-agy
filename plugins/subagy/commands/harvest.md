---
description: 收割 sub-agy 已完成作业的结果并验收
argument-hint: "[job-id ...]"
allowed-tools: Bash, Read
---

收割与验收 sub-agy 作业。

触发方式：
- 可由 watcher 完成通知自动触发（`dispatch` 挂的后台 watcher 退出时）。
- 也可由用户手动调用 `/subagy:harvest [job-id ...]`。

流程：
1. 若 $ARGUMENTS 为空，执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" status --all --json --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"`，找出 `state` 为 `done` 或 `error` 的作业。
2. 若用户指定了一个或多个 job-id，则只收割这些作业（仍建议先 status 确认它们已完成）。
3. 对每个目标作业：
   - 执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" result <job-id> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"` 拿到结构化验收单。
   - 用 Read 查看 result.json 中的 `files_changed_git`、`diff_stat`，必要时查看 worktree 中的具体文件或执行 `git diff`。
4. 输出汇总表，列：job_id / contract_ok / tests_passed / 验收结论 / 建议动作。

**动作规则（硬性）**：
- 验收不通过（`contract_ok` 为 false，或 `tests_passed` 为 false，或 diff/文件不符合预期）→ **自动**执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" feedback <job-id> "<具体、可操作的修复要求>"` 打回。这是设计目的：打回自动化。
- 验收通过 → **只**给出合并命令建议（例如 `git merge agy/<job-id>` 或 cherry-pick），**绝不自行合并、提交或清理**。合并与 cleanup 永远由用户手动决定。

**注意**：不要对 `running` 或 `queued` 作业执行 feedback。若目标仍在运行，告诉用户稍后再收割。
