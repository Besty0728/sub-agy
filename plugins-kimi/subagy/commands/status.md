---
description: 查看 sub-agy 作业状态
---

如果用户传入了 job id（`$ARGUMENTS`），请使用 Bash 工具执行：

```bash
sub-agy status $ARGUMENTS --pretty --cwd "<项目根>"
```

否则请执行：

```bash
sub-agy status --all --pretty --cwd "<项目根>"
```

展示字段包含作业状态、队列位次（queue，`#N` 表示排在第 N 位等运行槽位，`-` 表示已在跑或已终态）、已运行/耗费时间（elapsed）、tokens 消耗以及最近一步摘要。
将命令输出**逐字**贴进回复，不要总结、改写或省略内容。

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy status --all --pretty --cwd ./my-project
```
