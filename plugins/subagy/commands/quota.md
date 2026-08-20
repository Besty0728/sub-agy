---
allowed-tools: Bash
---

# /subagy:quota

查询 Antigravity CLI 的额度使用情况。该命令**不调用任何模型**，0 token，免费。

请使用 Bash 工具执行以下命令：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" quota --oneline
```

将命令输出**逐字**贴进回复，不要总结或改写。需要明细表格时用 `quota --pretty`。

## 输出示例

```
Gemini 模型：5h 限额剩余 99.8%（32分钟后重置），7d 限额剩余 99.8%（6天11小时后重置）；Claude/GPT 模型：7d 限额剩余 100.0%
```

## 说明

- 优先解析 `agy` 返回的结构化 `command.data.groups`。
- 若结构化数据缺失，自动降级解析 `response` 中的 TSV。
- 失败时返回 JSON `{"ok": false, "error": "..."}` 并 exit 1；`agy` 未安装则 exit 127。
