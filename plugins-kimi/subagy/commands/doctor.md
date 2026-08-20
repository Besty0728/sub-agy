---
description: 检查 sub-agy 运行环境。触发词：诊断、doctor、检查环境、检查依赖
---

请使用 Bash 工具执行：

```bash
sub-agy doctor --pretty
```

将命令输出**逐字**贴进回复，禁止总结或改写。

## 回退方式

如果 `sub-agy` 不在 PATH：

```bash
export SUB_AGY_HOME=/path/to/sub-agy
uv run --project "$SUB_AGY_HOME" sub-agy doctor --pretty
```
