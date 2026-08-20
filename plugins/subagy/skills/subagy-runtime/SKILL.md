---
name: subagy-runtime
description: sub-agy 异步作业执行后端的运行时契约、状态机与铁律
user-invocable: false
---

# sub-agy 运行时契约

sub-agy 是 Antigravity CLI (`agy`) 的异步作业封装层。Claude Code 通过插件命令把代码执行任务派发到后台，`agy` 在独立 git worktree 里执行，规划侧只负责编排，不占用 Claude 调用额度。

## 命令全集

- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" watch <job-id ...> [--interval 秒] [--timeout 时长] [--strict] [--pretty]`
  - 轮询每个作业直到全部进入终态。**barrier 语义**：传多个 id 时要等最后一个才退出，所以增量汇报场景请一个 id 一个 watcher。
  - 任一 id 不存在时立即 exit 3。
  - interval 越界（不在 0.5–60 秒之间）exit 64。
  - 全部终态时输出 JSON 数组，每个元素包含：`job_id, state, round, agy_status, summary, contract_ok, tests_passed, elapsed_seconds, tokens, diff_stat, result_path, events_path, worktree, branch`。`--pretty` 表格包含 `elapsed` 与 `tokens` 列。
  - 退出码：全部作业进入终态 → 0；job 不存在 → 3；超时 → 124。
  - `--strict`：把成败也编码进退出码——全部 `done` → 0，任一 `error`/`cancelled`/`interrupted` → 1。不传时沿用旧行为（只要终态就 0）。
  - `--timeout` 默认 60m，未把排队等待计入，排队靠后的作业要显式加大。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" run --plan <file.md> --cwd <project> [--model ...] [--effort ...] [--timeout ...] [--no-worktree] [--wait]`
  - 创建作业，写入 plan/prompt/schema/meta，spawn detached supervisor，默认立即返回 `job_id, state, queue_position, worktree, branch, events_log`。
  - **不会因并发上限失败**：超出 `max_concurrent` 时作业以 `queued` 落盘等槽位，`queue_position` 给出 1 起的 FIFO 位次（`null` = 已拿到槽位直接跑）。
  - 加 `--wait` 时不立即退出，原地等待该作业到终态，输出与 `watch` 相同的 JSON 对象并遵循相同退出码（含排队等待时间）。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" status <job-id> | --all [--pretty]`
  - 查看作业状态、队列位次、已运行时间、tokens 消耗、最近一步摘要。包含惰性 interrupted 和解。`--pretty` 表格包含 `queue`/`elapsed`/`tokens` 列。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" result <job-id> [--events]`
  - 输出 `result.json` + `git diff --stat`。作业未完成时 exit 4。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" feedback <job-id> "<message>"`
  - 在保留 conversation 的前提下启动新一轮修复。要求状态为 done/error 且 conversation_id 存在。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" cancel <job-id>`
  - 向 supervisor 发送 SIGTERM；由 supervisor 杀掉 agy 进程组并落 `cancelled` 状态。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" list [--state ...]`
  - 列出所有作业。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" cleanup <job-id> [--purge] [--delete-branch] [--force]`
  - 移除 worktree，可选删除分支与日志。默认拒绝清理 running/queued 作业。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" doctor [--pretty]`
  - 检查 agy/PATH/git/Python/配置。
- `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" quota [--pretty]`
  - 无头额度查询，0 token。优先解析结构化数据，缺失时降级解析 TSV。
  - 退出码：`0` 成功；`1` 失败；`127` agy 未安装。

## watcher 与主动通知

`dispatch` 在汇报完作业卡片后，应**为每个 job 各挂一个** Bash `run_in_background` 的 `watch <单个 job id> --strict --cwd <project>`。某个 watcher 退出即表示**那一个**作业进入终态，主 agent 会收到系统通知并只对该 job id 进入 `harvest.md` 的审查流程。

- **一 job 一 shell**：`watch` 是 barrier，多 id 共用一个 watcher 会让最快的作业被最慢的拖住，失去增量汇报的意义。
- 主 agent 提示语统一为：**每个作业一个独立 watcher，谁先完成我就先审谁**。
- `--strict` 让退出码直接区分成败（0 = done，1 = error/cancelled/interrupted），通知无需再解析 JSON 就能判断走向。
- watcher 完成通知只是触发器，实际审查仍必须遵守 harvest 的硬性规则。

## 并发与排队

- `max_concurrent`（默认 3）限制的是 **同时 `running`** 的作业数，不再是"能不能派发"。
- `run`/`feedback` 一律接受作业并 spawn detached supervisor；supervisor 在拉起 agy 之前先调用 `queue.acquire_slot`，拿不到槽位就以 `queued` 原地等。
- 槽位记账在 `<project>/.subagy/queue.lock` 上加 flock 串行化，两个 supervisor 不会抢到同一个槽位。
- 排队顺序按 `meta.queued_at` FIFO（旧 meta 无此字段时回落 `created_at`）。`feedback` 打回会刷新 `queued_at`，即重新排到队尾。
- 排队中的作业进 `events.ndjson` 一条 `{"type":"queued","running":N,"queued_ahead":M}` 事件。
- `queue_timeout`（默认 `2h`）是安全阀：等槽位超时 → `state=error`，`error` 写明等待时长。
- 排队中的作业可以直接 `cancel`，落 `cancelled` 而非 `error`（agy 从未被拉起）。
- exit code 5（`concurrency`）保留在退出码表里但 `run` 已不再产出。

## 设计约定

- **面向用户的展示型输出必须经 Bash 工具调用呈现**（工具调用对用户可见）。不要用内联执行（`` !`bash ...` ``）把结果藏进提示词。例如 `quota`、`status`、`doctor` 等展示型命令，应调用 Bash 工具执行 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" <子命令> --pretty`，再把输出逐字贴进回复。

## 作业状态机

`queued` → `running` → `done` | `error` | `cancelled` | `interrupted`

- `queued`：已创建，supervisor 尚未拿到运行槽位（或刚拿到还没拉起 agy）。`status` 会给出 `queue_position`。
- `running`：supervisor 已占住一个槽位并正在运行 agy。
- `done`：agy exit 0 且 status SUCCESS，已写 result.json。
- `error`：agy 非零退出、status ERROR/INVALID、无结果事件且无 transcript 兜底，或等槽位超过 `queue_timeout`。
- `cancelled`：用户主动 cancel（含还在排队、agy 尚未拉起时）。
- `interrupted`：惰性和解状态。当 `state=running`/`queued` 但 supervisor pid 已不存在且 `finished_at` 为空时，`status`/`list` 会现场改写为 `interrupted`。（`queued` 且 pid 尚未记录时不判定，避免误伤刚 spawn 的作业。）

## result.json 字段解释

```json
{
  "job_id": "...",
  "state": "done",
  "agy_status": "SUCCESS",
  "round": 1,
  "summary": "...",
  "structured_output": {...}|null,
  "contract_ok": true,
  "response_text": "agy 原始 response",
  "files_changed_git": ["..."],
  "diff_stat": "git diff --stat 输出",
  "usage": {...},
  "conversation_id": "...",
  "duration_seconds": 0,
  "num_turns": 0,
  "recovered_from_transcript": false,
  "attempts": 1,
  "worktree": "...",
  "branch": "agy/<id>",
  "base_sha": "..."
}
```

- `contract_ok`：结构化输出存在且满足 schema 时为 true；若 `round>=2` 因兼容性丢弃 schema，则为 false。
- `recovered_from_transcript`：agy stdout bug 触发，从 transcript 兜底恢复时为 true。
- `structured_output`：agy 按 JSON schema 返回的对象；缺失时 `contract_ok=false`。
- `response_text`：agy 原始文本响应。

## 反馈轮次语义

- `feedback` 会让 `round += 1`、状态回到 `queued`，并用 `--conversation <id>` 续会话。
- 作业的 `model` 与 `effort` 记录于 `meta.json`（`model`/`effort` 字段已存在），打回轮次复用同一档位与模型。
- 新一轮 prompt 包含上一轮 summary 与本次 message，要求 agy 修复后重新满足契约。
- 若 `round>=2` 时 `--json-schema` 导致参数错误，sub-agy 会去掉 schema 重试一次，并在 result 中标 `contract_ok=false, contract_note="schema dropped on round>=2"`。

## 退出码表

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 通用错误；`watch --strict` 下也表示有作业未 done |
| 3 | 作业不存在 |
| 4 | 作业未完成（result 时） |
| 5 | 超过并发上限（保留；`run` 改为排队后已不产出） |
| 6 | 需要 git 仓库但未找到 |
| 64 | CLI 用法错误/参数无效 |
| 124 | watcher/run --wait 超时 |
| 127 | agy 未安装 |

## `.subagy/` 目录结构

```
<project>/.subagy/
├── queue.lock             # 运行槽位记账的 flock 文件
├── jobs/<job_id>/
│   ├── meta.json
│   ├── plan.md
│   ├── prompt.txt
│   ├── schema.json
│   ├── events.ndjson
│   ├── stderr.log
│   └── result.json
└── worktrees/<job_id>/    # git worktree，分支 agy/<job_id>
```

## 铁律

1. **合并与 cleanup 由用户决定**：收割通过时只给出 `git merge agy/<job_id>` 或 cherry-pick 建议，绝不要自动合并、提交或清理。
2. **不打回未完成的作业**：feedback/cleanup/harvest 动作只针对 `done`/`error` 作业。看到 `running` 或 `queued` 请让用户等待或 `cancel`。
3. **不修改用户 agy 配置**：sub-agy 不读取/修改 `~/.gemini/antigravity-cli/settings.json`，只读取自己的 `~/.config/sub-agy/config.toml`。
4. **零 API key / 零代理**：sub-agy 只做本地进程编排，所有 LLM 调用都走用户本机已安装的 `agy`。
