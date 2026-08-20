---
allowed-tools: Bash
---

# /subagy:quota

查询 Antigravity CLI 的额度使用情况。该命令**不调用任何模型**，0 token，免费。

请使用 Bash 工具执行以下命令：

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ab" quota --pretty
```

将命令输出**逐字**贴进回复，不要总结、改写或省略表格本体。输出后可附加一行必要的中文注释（例如 `5h` 窗口用于平滑全球容量，`weekly` 与你的订阅档位挂钩）。

## 输出示例

```
[Gemini Models]
window          remaining% reset_time                  reset_in
5h                    99.8 2026-08-20T08:02:31Z        32 minutes
weekly                99.8 2026-08-26T18:35:04Z        6 days, 11 hours

[Claude and GPT models]
window          remaining% reset_time                  reset_in
weekly               100.0 2026-08-27T00:00:00Z        -

5h 窗口用于平滑全球容量；weekly 与你的订阅档位挂钩
```

## 说明

- 优先解析 `agy` 返回的结构化 `command.data.groups`。
- 若结构化数据缺失，自动降级解析 `response` 中的 TSV。
- 失败时返回 JSON `{"ok": false, "error": "..."}` 并 exit 1；`agy` 未安装则 exit 127。
