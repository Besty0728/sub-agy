---
description: 派发一个或多个计划给 sub-agy 异步执行
argument-hint: "<plan.md|raw task> [--model ...] [--effort ...] [--timeout ...] [--no-worktree]"
allowed-tools: Bash, Read, Write
---

把计划派发给 sub-agy 执行。

$ARGUMENTS 的解析规则：
1. 若第一个 token 是已存在的 `.md` 文件，则把所有存在的 `.md` token 视为计划文件，剩余 token 作为透传给 `sub-agy run` 的旗标。
2. 否则把整个 $ARGUMENTS 视为原始任务文本，先 Write 到 `.subagy/inbox/<时间戳>.md`（frontmatter 留空模板，方便后续补 scope/acceptance/constraints），再执行派发。

### 分档判定（硬性前置步骤，不可跳过）

**派发任何作业之前**，必须先逐个计划输出一份分档判定表。缺这张表就直接派发视为违约。

| plan | 复杂度信号 | effort | 理由 |
|---|---|---|---|

- **默认值**：model = `gemini-3.7-flash`（flash 系列，配额友好），timeout = `30m`。effort **没有默认值可躺**——每个计划都要显式判一次，判完是 `medium` 也要写进表里。
- **effort 判定依据**（按计划实际内容判，不是按感觉）：
  | 任务特征 | effort |
  |---|---|
  | 单文件小改、文案/文档、机械重命名、简单脚本 | `low` |
  | 常规多文件特性、带测试的修改 | `medium` |
  | 跨模块架构调整、复杂调试、需要长链路推理的计划 | `high` |
- 判定结果必须落到 `--effort <档位>` 旗标上**显式传**，不要靠默认值兜。这样 `meta.json` 与 `stderr.log` 的 argv 里都留了证，事后能复盘档位选得对不对。
- **自查**：若一批计划全判成同一档，回头再看一遍——同批复杂度全等是可能的，但更常见的是漏判。
- **model 抉择**：默认不换（`gemini-3.7-flash`）。仅当用户明确要求时更换；可用 slug 通过 `agy models` 查询（flash 系列优先）。
- **timeout 抉择**：按任务规模通过 `--timeout` 透传（默认 30m）。

对每一个计划文件：
- 读取项目根 `${CLAUDE_PROJECT_DIR:-$PWD}`。
- 执行：`bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" run --plan <file> --cwd "${CLAUDE_PROJECT_DIR:-$PWD}" --effort <判定档位> [其他透传旗标]`。
- 收集每个作业的 job_id、state、queue_position、branch、worktree。

批量派发时逐个 run，任意一个失败也不中断后续派发，但在最终汇总中标记失败原因。

**并发与排队**：`run` 不会再因并发上限而失败。超出 `max_concurrent`（默认 3）的作业以 `state=queued` 落盘，由它自己的 supervisor 按 `queued_at` FIFO 等槽位，前面的作业一结束就自动接位。所以想派几个就派几个。`run` 输出里 `queue_position` 为 `null` 表示已拿到槽位，为 `1/2/3...` 表示排在队列第几位。

输出格式：每个作业一张紧凑 markdown 卡片，包含 `档位: model=<slug> effort=<low|medium|high>`（显式所选值）、选择理由，以及 `排队: 立即执行 | 队列第 N 位`。（完成后 tokens 消耗与用时见 harvest）

### watcher：一个作业一个 shell

派发汇报完所有作业卡片后，**为每个 job 单独挂一个后台 watcher**——发起多次 Bash `run_in_background` 调用，每次只盯一个 job id：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" watch <单个 job id> --strict --cwd "${CLAUDE_PROJECT_DIR:-$PWD}" --timeout <见下>
```

- **绝不要把多个 job id 塞进同一个 watch**。`watch` 是 barrier 语义：只有全部作业进入终态才退出。多 id 共用一个 watcher 会让最快的作业被最慢的拖住，拿不到增量汇报。
- 每个 watcher 独立退出 → 独立通知 → 主 agent **只对那一个 job id** 走 `harvest.md` 流程，边完成边收割，不等其余作业。
- `--strict`：全 `done` 退出 0，任一 `error`/`cancelled`/`interrupted` 退出 1。单 job watcher 配上它，退出码本身就编码了成败，通知一眼可读。
- `--timeout` 要把排队等待算进去：`(该作业 timeout) × (queue_position 或 1) + 30m` 余量。默认 60m 对排队靠后的作业不够用。

提示语统一为：

> 作业已派发（N 个，其中 M 个排队中），每个作业一个独立 watcher，谁先完成我就先审谁。

某个 watcher 退出即表示**那一个**作业进入终态，主 agent 会收到系统通知并按 `harvest.md` 的流程与硬性规则自动进入审查——不要等用户喊，也不要等其他作业。

`dispatch` 自身仍只负责把作业放进队列并启动 detached supervisor，不原地轮询。
