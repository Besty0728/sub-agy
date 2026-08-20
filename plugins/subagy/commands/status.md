---
description: 查看 sub-agy 作业状态
argument-hint: "[job-id]"
allowed-tools: Bash
---

如果用户传入了 job id（`$ARGUMENTS`），请使用 Bash 工具执行：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" status $ARGUMENTS --pretty
```

否则请执行：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" status --all --pretty
```

将命令输出**逐字**贴进回复，不要总结、改写或省略内容。
