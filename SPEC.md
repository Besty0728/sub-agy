# sub-agy 引擎规格（v1.4）

> **rename 注记**：本项目自 v1.4 起从 `agy-bridge` 全局更名为 `sub-agy`。§1–§16 为 v1–v1.3 的设计史，保留原文中的旧名（`agy-bridge`、`agybridge`、`agy_bridge`、`AGY_BRIDGE_HOME`、`.agybridge`）以反映当时状态；当前实现的包名、CLI、插件与目录结构请见 §17。git 分支前缀 `agy/<job_id>` 作为执行体品牌继续保留。

> 给实现者的施工图纸。目标：把 Antigravity CLI（`agy`）变成规划侧 agent（Claude Code / Codex）的**异步作业执行后端**。纯进程编排，不接触任何 API key，不做网络请求。
>
> 本机实测事实（2026-08-20，agy 1.1.13，已实现者必须以此为准）：
> - `agy -p "<prompt>"` 无头单次执行；响应走 stdout，诊断走 stderr；成功 exit 0
> - `--model gemini-3.7-flash` 可用（slug 形式）；`--effort low|medium|high`
> - `--output-format text|json|stream-json`；json 信封字段：`conversation_id, status, response, error, duration_seconds, num_turns, structured_output, usage`（`structured_output` 仅在传 `--json-schema` 时出现）
> - `--json-schema` 接受 schema 字符串或 `.json` 文件路径；实测 `structured_output` 正确填充
> - `--conversation <id>` 续会话（**禁止用 `--continue`**，全局最近会话有并发竞争）
> - `--print-timeout`（默认 5m，Go duration 格式如 `30m`）；`--cwd <dir>`；`--dangerously-skip-permissions`
> - status 值：`SUCCESS|ERROR|CANCELED|INTERRUPTED|INVALID|WAITING|RUNNING`
> - **已知 bug**：非 TTY 下 `agy -p` 偶发"模型已响应但 stdout 为空"。兜底：读 `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`（该路径格式已实测存在），取最后一条 assistant 文本
> - 权限策略：`--dangerously-skip-permissions` 恒定追加（无人值守定位）

## 1. 形态与打包

- Python ≥3.11，**零第三方运行时依赖**（stdlib only：`argparse/json/subprocess/tomllib/pathlib/signal/os/time/shutil`）。dev 依赖只有 `pytest`。
- `pyproject.toml`：`[project] name = "agy-bridge"`，`[project.scripts] agy-bridge = "agy_bridge.cli:main"`，src 布局。
- **无守护进程**：CLI 无状态，状态全在磁盘。`run` 通过 spawn 一个 detached 的 `_supervise` 子进程（`start_new_session=True`）来托管 agy 子进程——调用方退出后作业继续；机器重启后靠 pid 活性检测做状态和解。

## 2. 目录与文件

每个项目工作区（--cwd 指定）：

```
<project>/.agybridge/
├── jobs/<job_id>/
│   ├── meta.json       # 作业记录（唯一事实源，schema 见 §3）
│   ├── plan.md         # 派发时计划快照
│   ├── prompt.txt      # 实际发送的完整 prompt（含契约段）
│   ├── schema.json     # 传给 --json-schema 的结果契约
│   ├── events.ndjson   # stream-json 原始事件（多轮追加；轮次间插 {"type":"round","n":N} 分隔行）
│   ├── stderr.log      # agy stderr
│   └── result.json     # 最新一轮聚合结果（schema 见 §6）
└── worktrees/<job_id>/ # git worktree，分支 agy/<job_id>
```

- `job_id` 格式：`j-YYYYMMDD-HHMMSS-<4位随机小写字母数字>`（时间可排序）。
- 首次在 git 仓库创建 worktree 前，把 `.agybridge/` 追加进 `.git/info/exclude`（若不存在则创建该文件）——**不碰用户的 .gitignore**。
- 时间一律 ISO-8601 UTC（`datetime.now(timezone.utc).isoformat()`）。

## 3. meta.json schema

```json
{
  "id": "j-...", "state": "queued|running|done|error|cancelled|interrupted",
  "created_at": "...", "started_at": "...", "finished_at": null,
  "round": 1, "pid_agy": null, "pid_supervisor": null,
  "project": "/abs", "worktree": "/abs/.agybridge/worktrees/<id>", "branch": "agy/<id>",
  "base_sha": "<创建时 git rev-parse HEAD>",
  "model": "gemini-3.7-flash", "effort": "medium", "timeout": "30m",
  "conversation_id": null, "agy_status": null, "exit_code": null,
  "error": null, "attempts": 0, "recovered_from_transcript": false
}
```

`interrupted` 是**惰性和解**状态：`status`/`list` 发现 `state=running` 但 supervisor pid 不活（`os.kill(pid, 0)` 抛 `ProcessLookupError`）且 `finished_at` 为空时，现场改写为 `interrupted`。

## 4. CLI 子命令（argparse；默认 JSON 输出到 stdout，`--pretty` 人类可读）

| 命令 | 关键参数 | 行为 |
|---|---|---|
| `run` | `--plan <file>` \| `--text <str>`（二选一必填）；`--cwd`（默认 pwd）；`--model/--effort/--timeout`（覆盖配置）；`--no-worktree`；`--no-schema` | 校验→建 worktree→写 plan/prompt/schema/meta→spawn detached `_supervise`→**立即**打印 `{job_id, worktree, branch, events_log}` 并 exit 0 |
| `status` | `<id>` \| `--all`；`--json`；`--pretty` | state、round、elapsed、tokens（input/output/total）、最近一条 step 摘要（events.ndjson 尾部解析）；含惰性 interrupted 和解。`--pretty` 输出带 elapsed（m:ss）与 tokens（k/M）列 |
| `result` | `<id>`；`--events`（附原始事件路径） | 打印 result.json 内容（含 `usage`）+ 现场计算 `git diff --stat <base_sha>..HEAD`；作业未完成时 exit 4 并打印当前状态 |
| `feedback` | `<id>` `<message>`；`--timeout` | 要求 meta.conversation_id 非空且 state 为 done/error；round+=1，spawn `_supervise --round N`，立即返回 |
| `cancel` | `<id>` | SIGTERM 到 supervisor pid（其信号处理器负责杀 agy 进程组并落 cancelled）；supervisor 已死但 agy 活着则直接杀 agy pgid |
| `list` | `--state` 过滤 | 扫描 jobs/，表格或 JSON |
| `cleanup` | `<id>`；`--purge`（连 jobs/<id> 日志目录一起删）；`--delete-branch` | `git worktree remove --force` + 可选 `git branch -D agy/<id>`；拒绝清理 running 作业除非 `--force` |
| `doctor` | — | 检查 agy 在 PATH、`agy --version` ≥1.1.8、git/python 版本、auth 痕迹（`~/.config/antigravity` 或 `~/.gemini/antigravity-cli` 目录存在）、配置可加载；打印 JSON 报告，有问题 exit 1 |
| `quota` | — | 查询 Antigravity 额度（0 token）；默认 JSON，支持 `--oneline` 一句话与 `--pretty` 表格；agy 未安装 exit 127，失败 exit 1 |
| `_supervise` | `<id>` `--round N` | 内部隐藏命令（help 中不显示） |

退出码约定：`0` 成功；`1` 通用错误；`3` 作业不存在；`4` 作业未完成；`5` 超并发上限；`6` 需要 git 仓库但不是；`64` CLI 用法错误；`127` agy 未安装。错误信息走 stderr，stdout 尽量仍有 JSON `{"error": ...}`。

## 5. run / _supervise 流程

`run`：
1. 解析配置 `~/.config/agy-bridge/config.toml`（tomllib；全部可选，缺省见 §8），CLI flag 覆盖。
2. 解析计划文件 frontmatter（§7）。
3. worktree：默认启用。`git -C <cwd> rev-parse --git-dir` 失败 → 若用户未显式 `--no-worktree`，警告并降级为直接在 cwd 执行（meta.worktree=null）。启用时：`base_sha=$(git rev-parse HEAD)`；`git worktree add <project>/.agybridge/worktrees/<id> -b agy/<id> <base_sha>`。
4. 并发闸：统计本项目 jobs 中 state=running/queued 数量，≥ `max_concurrent`（默认 3）→ exit 5。
5. 组装 prompt（§7 模板），写文件，spawn：`[sys.executable, "-m", "agy_bridge.cli", "_supervise", id]`，`start_new_session=True`，stdout/stderr 重定向到 stderr.log 追加。立即返回。

`_supervise`（托管一个 round）：
1. 更新 meta（state=running, pid_supervisor, round）。
2. 组装 agy argv（列表形式，无 shell）：
   ```
   [agy_bin, "-p", prompt, "--cwd", worktree_or_cwd,
    "--output-format", "stream-json", "--model", model, "--effort", effort,
    "--print-timeout", timeout, "--json-schema", str(schema_path)]
   # round≥2: 追加 ["--conversation", conversation_id]
   # 无条件追加 ["--dangerously-skip-permissions"]（无人值守执行）
   ```
3. `subprocess.Popen`（`start_new_session=True`，stdout=PIPE 逐行读写 events.ndjson，stderr 写 stderr.log）。记录 pid_agy。
4. 墙钟上限 = timeout 解析为秒 + 60s 宽限；超时 → 杀进程组（`os.killpg`）→ **换新进程重试**，最多 `max_retries`（默认 1）次 → 仍失败 state=error。
5. 逐行解析 stream-json；找 `type=="result"` 的终态事件（含信封字段）。
6. **stdout bug 兜底**：若无 result 事件或 response 为空 → 在 `~/.gemini/antigravity-cli/brain/` 下按 mtime 找作业启动窗口内最新的 `<uuid>/.system_generated/logs/transcript.jsonl`，提取最后一条 assistant 文本作为 response，meta.recovered_from_transcript=true。
7. 写 result.json（§6），更新 meta（state=done（exit 0 且 agy_status=SUCCESS）否则 error、conversation_id、usage、exit_code、finished_at）。
8. SIGTERM/SIGINT 处理器：杀 agy 进程组，state=cancelled，写 meta 后退出。
9. **优雅降级**：round≥2 若带 `--json-schema` 的调用以参数错误失败（组合兼容性未实测），去掉该 flag 重试一次并在 result 标 `contract_ok=false, contract_note="schema dropped on round≥2"`。

prompt 体积上限 200KB（ARG_MAX 安全线内），超出 exit 64 并说明。

## 6. result.json schema

```json
{
  "job_id": "...", "state": "done", "agy_status": "SUCCESS", "round": 1,
  "summary": "...", "structured_output": {...}|null, "contract_ok": true,
  "response_text": "<agy 原始 response>",
  "files_changed_git": ["<git diff --name-only base..HEAD>"],
  "diff_stat": "<git diff --stat 输出>",
  "usage": {...}, "conversation_id": "...",
  "duration_seconds": 0, "num_turns": 0,
  "recovered_from_transcript": false, "attempts": 1,
  "worktree": "...", "branch": "agy/<id>", "base_sha": "..."
}
```

`summary` 取值顺序：`structured_output.summary` → `response_text` 前 500 字符。`structured_output` 缺失时 `contract_ok=false`。

## 7. 计划文件与 prompt 契约

计划文件 frontmatter（手写极简解析器，**不用 PyYAML**；仅支持 `key: scalar` 与 `key:` 后跟 `- item` 列表两种形态；解析失败仅告警并只用正文）：

```markdown
---
scope: [src/auth/**]        # 行内列表或逐行 "- x" 均可
acceptance:
  - pytest tests/auth -q 通过
  - 不修改 scope 外文件
constraints:
  - 不新增依赖
---
# 正文（背景、任务分解……）
```

prompt 模板（supervisor 组装；round 1）：

```
<plan 正文>

---
## 执行契约（agy-bridge 注入，必须遵守）
- 你在一个专用 git worktree 中非交互执行：<worktree 或 cwd>。只在此目录内工作。
- 范围限制：只允许修改 <scope 列表，无则写"未声明，自行保守判断"> 内的文件。
- 结束前必须运行并通过验收检查：<acceptance 逐条，无则写"未声明">
- 约束：<constraints 逐条，无则写"无">
- 结束前用 conventional commit 把你的改动提交到当前分支。
- 最终回答必须满足附带的 JSON schema（structured_output）：summary、files_changed、tests_ran、tests_passed、acceptance_met、blockers、followups。
```

round ≥2 模板：`上一轮反馈：<message>\n\n上一轮 summary：<prev summary>\n\n请修复后重新满足上述契约。`（契约段同样附上）

结果契约 schema（写 schema.json 传 `--json-schema`；`additionalProperties: false`，required=`["summary","files_changed","tests_passed"]`）：

```json
{"type":"object","properties":{
 "summary":{"type":"string"},
 "files_changed":{"type":"array","items":{"type":"string"}},
 "tests_ran":{"type":"array","items":{"type":"string"}},
 "tests_passed":{"type":"boolean"},
 "acceptance_met":{"type":"array","items":{"type":"string"}},
 "blockers":{"type":"array","items":{"type":"string"}},
 "followups":{"type":"array","items":{"type":"string"}}},
 "required":["summary","files_changed","tests_passed"]}
```

## 8. 配置文件（全部可选）

`~/.config/agy-bridge/config.toml`：

```toml
default_model = "gemini-3.7-flash"
default_effort = "medium"
default_timeout = "30m"     # Go duration
max_concurrent = 3          # 每项目
max_retries = 1
agy_bin = "agy"
```

timeout 解析支持 `Ns/Nm/Nh`（`90s`/`30m`/`1h`），非法值 exit 64。

## 9. 安全默认

- agy 永远以 `--dangerously-skip-permissions` 启动；sub-agy 的安全边界 = git worktree 隔离 + 合并永远人工 + 计划侧 scope/constraints 约束。
- 不读取/不修改用户任何 agy 配置文件。

## 10. 测试（pytest，全部不依赖真实 agy）

关键手法：**fake agy 脚本**——一个 bash/python fixture 脚本，按参数输出预置的 stream-json 事件序列（含 result 事件）到 stdout，把它作为 `agy_bin` 传入，即可端到端测 run→status→result→feedback→cancel→cleanup。

- frontmatter 解析：标量/行内列表/多行列表/畸形输入
- timeout 解析：合法/非法
- ndjson 聚合：正常 result 事件；缺 result 事件（触发 transcript 兜底，用 fixture 目录伪造 brain 布局——用 `HOME` 环境变量指向 tmp 目录隔离）
- worktree 生命周期：tmp git repo 建/删 + `.git/info/exclude` 写入
- 并发闸：伪造 running 作业 meta 后 run 被拒（exit 5）
- cancel：fake agy 是 `sleep` 脚本，验证进程组被清理
- 惰性 interrupted：写 running meta 但 pid 指向不存在进程
- `_supervise` 对 fake agy 非零 exit → state=error；超时 → 重试逻辑（把 timeout 调小）

## 11. 真实冒烟（scripts/smoke.sh，单独交付）

tmp git 仓库 → 计划文件（"创建 hello.txt 内容为 agy-bridge-smoke，验收：test -f hello.txt 且内容正确，然后提交"）→ `run --effort low --timeout 5m` → 轮询 `status` 至 done → `result` 断言 hello.txt 已提交且 contract_ok=true → `feedback` 一轮（"把内容改为 agy-bridge-smoke-2 并 amend 提交"）→ 断言 → `cleanup --purge --delete-branch`。全程 set -euo pipefail，每步 echo。

## 12. 明确不做（v1）

MCP shim、Stop hook、跨项目调度、`--agent` 支持（`agy agents` 本机空输出，待查）、图像生成、plans 的远程分发。

## 14. watch 与主动通知（v1.1）

### 14.1 设计动机

Claude Code 的 Bash 工具在后台进程退出时会自动唤醒主 agent。v1.1 利用这一机制，让 `dispatch` 在派发作业后挂一个后台 `watch` 进程；watcher 在所有作业进入终态后退出，从而**主动通知**主 agent 进入收割审查，无需用户手动喊 harvest。

### 14.2 watcher 与 detached supervisor 的关系

- detached supervisor 是底座：它让 `agy` 在调用方退出后仍继续执行。
- watcher 是糖：它只是在调用方侧原地/后台轮询 meta，直到作业完成；本身不托管 agy 进程。
- `run` 默认仍立即返回 job_id；加 `--wait` 后则复用 watcher 逻辑，把“派发+等待”串成同步调用，供 Codex/脚本使用。

### 14.3 `watch` 命令

```
agy-bridge watch <id> [id...] [--cwd P] [--interval 秒, 默认 2] [--timeout 时长, 默认 60m] [--pretty|--json]
```

- 轮询每个 job 的 meta（复用 `reconcile_state` 做惰性和解），直到**全部**进入终态（done/error/cancelled/interrupted）。
- 任一 id 不存在 → stderr 报错 + exit 3（立即，不进入轮询）。
- `--timeout` 用 `config.parse_timeout` 同款解析（支持 `90s`/`30m`/`1h`）；超时后仍打印各作业当前状态，exit 124。
- interval 校验：0.5–60 秒，越界 exit 64。
- 全部终态时打印 JSON 数组，每个作业一个对象：
  ```json
  {
    "job_id": "...", "state": "done", "round": 1,
    "agy_status": "SUCCESS", "summary": "...",
    "contract_ok": true, "tests_passed": true,
    "elapsed_seconds": 12.3,
    "tokens": {
      "input": 1234,
      "output": 567,
      "total": 1801
    },
    "diff_stat": "...",
    "result_path": "...", "events_path": "...",
    "worktree": "...", "branch": "agy/..."
  }
  ```
  数据优先来自 `result.json`；缺失字段用 meta 兜底，`tokens` 来自 `result.json` 的 `usage`（无 result 时为 `null`），`diff_stat` 缺失给空串。
- 退出码：全部作业进入终态即返回 0；作业不存在返回 3；超时返回 124。
- `--pretty`：紧凑表格（`job_id`、`state`、`round`、`elapsed`、`tokens`、`contract_ok`、`summary` 截断 60 字符；`elapsed` 按 `m:ss`、`tokens` 按 `k`/`M` 人性化显示）。

### 14.4 `run --wait`

`agy-bridge run ... --wait` 在 spawn detached supervisor 后不立即退出，就地进入单 id 的 watcher 等待逻辑（strict 模式：全部 done 才返回 0，有非 done 终态返回 1）。终态后打印与 `watch` 相同的 JSON 对象。

### 14.5 退出码表（v1.1 追加）

| 码 | 含义 |
|---|---|
| 124 | `watch`/`run --wait` 超时 |

其余退出码保持不变，见 §4。

### 14.6 插件层约定

- `dispatch` 汇报完作业卡片后，用 Bash `run_in_background` 启动 `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" watch <所有新 job id> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}"`。
- watcher 退出时主 agent 自动收到通知，随即按 harvest.md 的流程与硬性规则自动审查。
- 主 agent 提示语统一为：**watcher 已挂，完成时我会自动开始审查**。

## 15. Codex 接入（v1.2）

### 15.1 形态

Codex 桌面端通过 **技能插件** 接入：

```
plugins-codex/agybridge/
├── .codex-plugin/plugin.json      # 插件清单
└── skills/
    ├── agybridge-dispatch/SKILL.md
    ├── agybridge-harvest/SKILL.md
    └── agybridge-runtime/SKILL.md
```

仓库根 `.agents/plugins/marketplace.json` 注册该插件，用户在 `~/.codex/config.toml` 把仓库根注册为本地市场后即可在 Codex 插件市场中安装。

### 15.2 与 Claude Code 插件的差异

| 维度 | Claude Code 插件 | Codex 技能插件 |
|---|---|---|
| 调用形态 | `/agybridge:dispatch` 等 slash 命令 | `agybridge-dispatch` 等 skill |
| 后台通知 | Bash `run_in_background` + watcher 退出唤醒主 agent | Codex Bash 调用被平台超时截断风险，因此 dispatch 后**原地阻塞 `watch`** |
| 安装方式 | `.claude-plugin/marketplace.json` + `/plugin marketplace add` | `.agents/plugins/marketplace.json` + `~/.codex/config.toml` 本地市场 |
| 命令前缀 | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" ...` | 直接使用 `agy-bridge ...`（假定已 `uv tool install`） |

### 15.3 技能职责

- **`agybridge-dispatch`**：
  1. 若输入不是已有 `.md` 文件，先写入 `.agybridge/inbox/<timestamp>.md`（带 scope/acceptance/constraints 空模板）。
  2. 逐个执行 `agy-bridge run --plan <file> --cwd <项目根> [flags]`。
  3. 收集所有 job_id 后执行 `agy-bridge watch <id...> --cwd <根> --timeout <默认 30m>` 阻塞等待。
  4. 若 Codex 的 shell 调用被平台超时截断，重新执行 `agy-bridge watch <id>` 续等（幂等）。
  5. `watch` 返回后按 `state`/`contract_ok`/`tests_passed` 进入 harvest 规则。

- **`agybridge-harvest`**：
  1. `status --all` 找出 done/error 作业（或按用户指定 id）。
  2. `result <id>` 拿验收单。
  3. 查看 `files_changed_git`/`diff_stat`，必要时看 worktree 文件。
  4. 输出汇总表（job_id / contract_ok / tests_passed / 结论 / 建议动作）。
  5. 验收不通过 → 自动 `feedback <id> "<具体修复要求>"`（仅 done/error）。
  6. 验收通过 → 只给 `git merge agy/<id>` 建议，绝不自行合并/提交/cleanup。
  7. 不对 running/queued 作业 feedback。

- **`agybridge-runtime`**：运行时契约参考，包括 CLI 全命令与 flag 表、状态机、result.json 字段、退出码表、`.agybridge/` 目录结构、agy 侧已知坑。

### 15.4 市场清单

仓库根 `.agents/plugins/marketplace.json`：

```json
{
  "name": "agybridge-marketplace",
  "interface": {"displayName": "agy-bridge Marketplace"},
  "plugins": [
    {
      "name": "agybridge",
      "source": {"source": "local", "path": "./plugins-codex/agybridge"},
      "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
      "category": "Developer Tools"
    }
  ]
}
```

用户注册本地市场：

```toml
[marketplaces.agybridge]
source_type = "local"
source = "/path/to/agy-bridge"
```

### 15.5 注意事项

- `policy.authentication: ON_INSTALL` 照抄官方样例；实际安装时 UI 是否提示认证由 Codex 桌面端决定，存在不确定性。
- Codex 技能内命令直接使用 `agy-bridge`，不再使用 `${CLAUDE_PLUGIN_ROOT}` 这类 Claude 专用变量。
- 未安装 `agy-bridge` 时，技能内提供回退：`export AGY_BRIDGE_HOME=<repo>` 或使用 `uv run --project <repo> agy-bridge ...`。

## 13. 交付物清单

| 交付物 | 状态 |
|---|---|
| `pyproject.toml` | 已实现 |
| `src/agy_bridge/__init__.py` | 已实现 |
| `src/agy_bridge/cli.py` | 已实现 |
| `src/agy_bridge/config.py` | 已实现 |
| `src/agy_bridge/plan.py` | 已实现 |
| `src/agy_bridge/jobs.py` | 已实现 |
| `src/agy_bridge/supervise.py` | 已实现 |
| `src/agy_bridge/worktree.py` | 已实现 |
| `src/agy_bridge/schema.py` | 已实现 |
| `src/agy_bridge/watch.py` | 已实现 |
| `src/agy_bridge/quota.py` | v1.3 已实现 |
| `plugins/agybridge/` | Claude Code 插件，已实现 |
| `plugins-codex/agybridge/` | Codex 技能插件，已实现 |
| `.agents/plugins/marketplace.json` | Codex 市场清单，已实现 |
| `codex/AGENTS.md.snippet` | Codex CLI 兜底指南，已实现 |
| `LICENSE` | MIT，v1.3 已补 |
| `README.md` | 已实现 |
| `SPEC.md` | 已实现 |
| `scripts/smoke.sh` | 已实现 |
| `tests/` | 已实现 |

## 16. quota（v1.3）

### 16.1 数据源

`agy-bridge quota` 通过调用本地已安装的 `agy` 获取额度：

```bash
agy -p "/usage" --output-format json --print-timeout 60s
```

实测特征：

- 该调用**消耗 0 token**，免费。
- **不要**传 `--model`/`--effort`，否则可能改变响应形态或产生额度消耗。
- 成功时 stdout 为一个 JSON 信封，至少包含 `status`、`usage`、`response`；若 agy 支持，还会包含 `command.data.groups` 结构化数据。

### 16.2 结构化数据（source: structured）

当信封中存在 `command.data.groups` 时，优先使用结构化数据：

```json
{
  "command": {
    "data": {
      "groups": [
        {
          "name": "Gemini Models",
          "buckets": [
            {
              "id": "gemini-5h",
              "name": "Five Hour Limit Remaining",
              "description": "...it will fully refresh in 32 minutes.",
              "window": "5h",
              "remaining_fraction": 0.998,
              "reset_time": "2026-08-20T08:02:31Z"
            }
          ]
        }
      ]
    }
  }
}
```

归一化规则：

- `remaining_pct = round(remaining_fraction * 100, 1)`。
- `reset_in` 从 `description` 中用正则 `refresh in (.+?)\.?$`（忽略大小写）提取；提取不出给 `null`。
- 保留 `window` 原值（通常为 `5h` 或 `weekly`）和 `reset_time` 原值。

### 16.3 文本降级（source: text_fallback）

若信封无 `command.data`，则解析 `response` 字段中的 TSV。每行四列：

```
<组名>\t<桶名>\t<百分比>\t<重置时间>
```

例如：

```
Gemini Models\tFive Hour Limit Remaining\t99.8%\t2026-08-20T08:02:31Z
Gemini Models\tWeekly Limit Remaining\t99.8%\t2026-08-26T18:35:04Z
```

桶名归一化：

- `Five Hour Limit Remaining` → `5h`
- `Weekly Limit Remaining` → `weekly`
- 其它按包含 "week"/"hour"/"5h" 做启发式映射。

此模式下 `reset_in` 恒为 `null`。

### 16.4 双窗口语义

Antigravity 额度分为两个窗口：

- `5h`：短周期滚动窗口，用于平滑全球容量。
- `weekly`：长周期窗口，与用户的订阅档位挂钩。

`--pretty` 模式下，输出末尾附带图例：

```
5h 窗口用于平滑全球容量；weekly 与你的订阅档位挂钩
```

### 16.5 失败与退出码

| 场景 | 行为 | 退出码 |
|---|---|---|
| 成功 | 输出 `{ok: true, source: ..., groups: [...]}` 或 pretty 表格 | 0 |
| 解析失败 / agy 非零退出 / 输出垃圾 | 输出 `{ok: false, error: ...}` | 1 |
| `agy` 未找到 | 输出 `{ok: false, error: ...}` | 127 |

墙钟超时设置为 75 秒，以覆盖 `agy` 侧 `--print-timeout 60s` 的上限。


## 17. rename: sub-agy（v1.4）

v1.4 仅做项目级重命名，功能与 v1.3 完全一致。映射如下：

| 旧 | 新 |
|---|---|
| Python 包 `agy_bridge`（`src/agy_bridge/`） | `sub_agy`（`src/sub_agy/`） |
| PyPI 项目名 / CLI 命令 `agy-bridge` | `sub-agy` |
| Claude 插件目录 `plugins/agybridge/` 及插件名 | `plugins/subagy/`，name `subagy` |
| Codex 插件目录 `plugins-codex/agybridge/` 及插件名 | `plugins-codex/subagy/`，name `subagy` |
| 技能名 `agybridge-*` / `agy-bridge-runtime`（两侧 `skills/` 目录名 + `SKILL.md` 的 `name` 字段） | `subagy-*` / `subagy-runtime` |
| 运行时状态目录 `.agybridge/` | `.subagy/` |
| 配置目录 `~/.config/agy-bridge/` | `~/.config/sub-agy/` |
| 环境变量 `AGY_BRIDGE_HOME` | `SUB_AGY_HOME` |
| 测试挂钩 `_AGY_BRIDGE_GRACE_SECONDS` | `_SUB_AGY_GRACE_SECONDS` |
| 测试/调试环境变量 `AGY_BRIDGE_CONFIG` | `SUB_AGY_CONFIG` |
| 市场清单 name：`.claude-plugin/marketplace.json` | `subagy` |
| 市场清单 name：`.agents/plugins/marketplace.json` | `subagy-marketplace` |
| 仓库路径示例 `/Users/betsy/CodeSpace/agy-bridge` | `/Users/betsy/CodeSpace/sub-agy` |

保持不变（未改名）：

- `agy` 二进制本身：`agy_bin`、`agy -p`、`agy --help`、`agy agents` 等。
- `~/.gemini/antigravity-cli/` 路径、transcript/brain 路径。
- git 分支前缀 `agy/<job_id>`。
- 作业 id 前缀 `j-`。
- 致谢/参考章节里的外部仓库名（`antigravity-plugin-cc`、`agy-mcp` 等）。
- §1–§16 历史正文。
