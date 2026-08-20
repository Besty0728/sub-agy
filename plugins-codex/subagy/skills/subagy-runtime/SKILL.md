---
name: sub-agy-runtime
description: |
  This skill provides the runtime contract, state machine, result schema, exit codes, and hard rules for the sub-agy asynchronous job execution backend.
  Triggers on: "sub-agy runtime", "作业状态机", "result.json", "exit code", "状态", "contract", "watch timeout", "transcript recovery", "no worktree".
  Reference this whenever precise CLI semantics, job lifecycle, or result interpretation are needed.
---

# sub-agy 运行时契约

sub-agy 是 Antigravity CLI (`agy`) 的异步作业封装层。Codex 通过技能把代码执行任务派发到后台，`agy` 在独立 git worktree 里执行，规划侧只负责编排，不占用 Codex 调用额度。

## CLI 命令全集与 flag 表

| 命令 | 关键参数 | 行为 |
|---|---|---|
| `run` | `--plan <file>` \| `--text <str>`（二选一必填）；`--cwd`；`--model/--effort/--timeout`；`--no-worktree`；`--no-schema`；`--wait` | 创建作业并 spawn detached supervisor。默认立即返回 `job_id`；`--wait` 会原地等待到终态并输出与 `watch` 相同的 JSON。 |
| `status` | `<id>` \| `--all`；`--state`；`--pretty` | 查看作业状态、已运行时间、tokens 消耗、最近一步摘要。包含惰性 interrupted 和解。`--pretty` 表格包含 `elapsed` 与 `tokens` 列。 |
| `result` | `<id>`；`--events`；`--pretty` | 输出 `result.json`（含 `usage`）+ `git diff --stat`。作业未完成时 exit 4。 |
| `watch` | `<id> ...`；`--cwd`；`--interval`（默认 2，范围 0.5–60 秒）；`--timeout`（默认 60m）；`--pretty` | 轮询直到全部作业进入终态。输出含 tokens 字段。超时 exit 124；全部终态 → 0。`--pretty` 表格包含 `elapsed` 与 `tokens` 列。 |
| `feedback` | `<id> "<message>"` | 在保留 conversation 的前提下启动新一轮修复。要求状态为 done/error 且 conversation_id 存在。 |
| `cancel` | `<id>` | 向 supervisor 发送 SIGTERM；supervisor 负责杀掉 agy 进程组并落 `cancelled` 状态。 |
| `list` | `--state`；`--pretty` | 列出所有作业。 |
| `cleanup` | `<id>`；`--purge`；`--delete-branch`；`--force` | 移除 worktree，可选删除分支与日志。默认拒绝清理 running/queued 作业。 |
| `doctor` | `--pretty` | 检查 agy/PATH/git/Python/配置。 |
| `quota` | `--pretty` | 无头额度查询，0 token；失败 exit 1，agy 未安装 exit 127。 |
| `_supervise` | `<id> --round N` | 内部隐藏命令，help 中不显示。 |

## 作业状态机

```
queued → running → done | error | cancelled | interrupted
```

- `queued`：已创建，supervisor 尚未把 agy 拉起。
- `running`：supervisor 正在运行 agy。
- `done`：agy exit 0 且 status SUCCESS，已写 result.json。
- `error`：agy 非零退出、status ERROR/INVALID，或无结果事件且无 transcript 兜底。
- `cancelled`：用户主动 cancel。
- `interrupted`：惰性和解状态。当 `state=running` 但 supervisor pid 已不存在且 `finished_at` 为空时，`status`/`list`/`watch` 会现场改写为 `interrupted`。

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
- `summary` 取值顺序：`structured_output.summary` → `response_text` 前 500 字符。

## 退出码表

| 码 | 含义 |
|---|---|
| 0 | 成功；watch 作业全部进入终态 |
| 1 | 通用错误；run --wait 中有任何 error/cancelled/interrupted 作业 |
| 3 | 作业不存在 |
| 4 | 作业未完成（result 时） |
| 5 | 超过并发上限 |
| 6 | 需要 git 仓库但未找到 |
| 64 | CLI 用法错误/参数无效 |
| 124 | `watch`/`run --wait` 超时 |
| 127 | agy 未安装 |

## `.subagy/` 目录结构

```
<project>/.subagy/
├── inbox/                  # 原始任务文本落盘目录
├── jobs/<job_id>/
│   ├── meta.json           # 作业记录（唯一事实源）
│   ├── plan.md             # 派发时计划快照
│   ├── prompt.txt          # 实际发送的完整 prompt
│   ├── schema.json         # 传给 --json-schema 的结果契约
│   ├── events.ndjson       # stream-json 原始事件
│   ├── stderr.log          # agy stderr
│   └── result.json         # 最新一轮聚合结果
└── worktrees/<job_id>/     # git worktree，分支 agy/<job_id>
```

## agy 侧已知坑

- **stdout bug 兜底**：非 TTY 下 `agy -p` 偶发"模型已响应但 stdout 为空"。sub-agy 内置兜底：读 `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`，取最后一条 assistant 文本。触发时 `result.recovered_from_transcript=true`。
- **禁止 `--continue`**：agl-bridge 使用 `--conversation <id>` 续会话，因为全局最近会话有并发竞争。
- **权限策略**：sub-agy 恒以 `--dangerously-skip-permissions` 启动 agy 实现无人值守；安全边界由 git worktree 隔离 + 人工合并保障。
- **round≥2 schema 降级**：若带 `--json-schema` 的调用在 round≥2 以参数错误失败，sub-agy 会去掉 schema 重试一次，并在 result 标 `contract_ok=false, contract_note="schema dropped on round>=2"`。

## 反馈轮次语义

- `feedback` 会让 `round += 1`、状态回到 `queued`，并用 `--conversation <id>` 续会话。
- 作业的 `model` 与 `effort` 记录于 `meta.json`（`model`/`effort` 字段已存在），打回轮次复用同一档位与模型。
- 新一轮 prompt 包含上一轮 summary 与本次 message，要求 agy 修复后重新满足契约。

## 铁律

1. **合并与 cleanup 由用户决定**：收割通过时只给出 `git merge agy/<job_id>` 或 cherry-pick 建议，绝不要自动合并、提交或清理。
2. **不打回 running 作业**：feedback/cleanup/harvest 动作只针对 `done`/`error` 作业。看到 `running` 请让用户等待或 `cancel`。
3. **不修改用户 agy 配置**：sub-agy 不读取/修改 `~/.gemini/antigravity-cli/settings.json`，只读取自己的 `~/.config/sub-agy/config.toml`。
4. **零 API key / 零代理**：sub-agy 只做本地进程编排，所有 LLM 调用都走用户本机已安装的 `agy`。

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy --help
```
