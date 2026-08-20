---
description: 派发一个或多个计划给 sub-agy 异步执行。触发词：派发、dispatch、delegate、派给 agy、异步执行、后台执行、antigravity
---

把计划派发给 sub-agy 执行。

## 输入解析

$ARGUMENTS 的解析规则：
1. 若第一个 token 是已存在的 `.md` 文件，则把所有存在的 `.md` token 视为计划文件，剩余 token 作为透传给 `sub-agy run` 的旗标。
2. 否则把整个 $ARGUMENTS 视为原始任务文本，先写到 `.subagy/inbox/<时间戳>.md`（frontmatter 留空模板，方便后续补 scope/acceptance/constraints），再执行派发。

## 分档判定（硬性前置步骤，不可跳过）

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
- 项目根 = 当前会话工作目录（向上最近含 `.git` 的目录）。
- 执行：`sub-agy run --plan <file> --cwd "<项目根>" --effort <判定档位> [其他透传旗标]`。
- 收集每个作业的 job_id、state、queue_position、branch、worktree。

批量派发时逐个 run，任意一个失败也不中断后续派发，但在最终汇总中标记失败原因。

## 并发与排队

`run` 不会再因并发上限而失败。超出 `max_concurrent`（默认 3）的作业以 `state=queued` 落盘，由它自己的 supervisor 按 `queued_at` FIFO 等槽位，前面的作业一结束就自动接位。所以想派几个就派几个。`run` 输出里 `queue_position` 为 `null` 表示已拿到槽位，为 `1/2/3...` 表示排在队列第几位。

输出格式：每个作业一张紧凑 markdown 卡片，包含 `档位: model=<slug> effort=<low|medium|high>`（显式所选值）、选择理由，以及 `排队: 立即执行 | 队列第 N 位`。（完成后 tokens 消耗与用时见 harvest）

## AgentTeam 等效机制

派发汇报完所有作业卡片后，**为每个 job 单独派发一个后台 subagy-watcher subagent**——多 job 用 AgentSwarm 并行派发，每次只盯一个 job id：

```
subagy-watcher <单个 job id> <项目根> <超时时长>
```

- **绝不要把多个 job id 塞进同一个 watcher**。`watch` 是 barrier 语义：只有全部作业进入终态才退出。多 id 共用一个 watcher 会让最快的作业被最慢的拖住，拿不到增量汇报。
- 每个 watcher 独立退出 → 独立通知 → 主 agent **只对那一个 job id** 走 `harvest.md` 流程，边完成边收割，不等其余作业。
- watcher 会处理 `watch` 的 `--strict` 与超时重试（124 续等），最后一条消息必须是完整交付：watch 输出的 JSON 原文 + 一行判定（state/contract_ok/tests_passed）。

提示语统一为：

> 作业已派发（N 个，其中 M 个排队中），每个作业一个独立 watcher，谁先完成我就先审谁。

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy run --plan task.md --cwd ./my-project
```
