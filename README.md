# sub-agy

把 Antigravity CLI 变成 Claude Code / Codex 的异步代码执行后端。  
Plan in Claude Code / Codex, execute with Antigravity CLI (Gemini) — official CLIs only.

## 特性

- **异步派发后台执行**：`run` 立即返回 job_id，agy 在 detached supervisor 中继续完成。
- **git worktree 隔离**：每个作业在独立 worktree 运行，互不污染主分支。
- **结构化验收契约**：通过 `--json-schema` 要求 agy 返回 summary/files_changed/tests_passed 等字段。
- **完成主动通知**：watcher 后台轮询，作业全部进入终态后自动唤醒主 agent。
- **自动打回回路**：验收不通过时 `feedback` 保留 conversation，启动下一轮修复。
- **额度查询 0-token**：`quota` 免费查询 Antigravity 5h/weekly 双窗口剩余额度。

## 环境依赖

- **agy CLI ≥1.1.8** 且已交互登录过一次（跑一次 `agy` 完成 OAuth）
- **Python ≥3.11**
- **uv**（推荐）或 pipx
- **git**（worktree 隔离所需；非 git 项目自动降级为直跑）
- **Claude Code 或 Codex 桌面端**（至少其一）

## 安装

### CLI（无需 clone）

```bash
uv tool install git+https://github.com/Besty0728/sub-agy
```

验证安装：

```bash
sub-agy doctor
```

### Claude Code 插件

```text
/plugin marketplace add Besty0728/sub-agy
/plugin install subagy@subagy
/reload-plugins
```

### Codex 桌面端

```bash
git clone https://github.com/Besty0728/sub-agy
```

在 `~/.codex/config.toml` 添加：

```toml
[marketplaces.subagy]
source_type = "local"
source = "<clone 路径>"
```

重启 Codex，在插件界面安装 `subagy`。

## 快速上手

### 1. 写计划文件

```bash
cat > plan.md <<'EOF'
---
scope: [src/**/*.py]
acceptance:
  - pytest tests/ -q 通过
constraints:
  - 不新增依赖
---
给 login 函数加上类型注解并修复由此暴露出的类型错误。
EOF
```

### 2. 派发

在 Claude Code 中：

```text
/subagy:dispatch plan.md
```

或直接用 CLI：

```bash
sub-agy run --plan plan.md --cwd ./my-project
```

### 3. 自动审查

watcher 触发主 agent 后，按 `/subagy:harvest` 流程审查结果：通过、失败或打回。

### 4. 合并

验收合格后，手动合并执行分支：

```bash
git merge agy/<job-id>
```

### 额度查询

```bash
sub-agy quota --pretty
```

示例输出：

```
[Gemini Models]
window          remaining% reset_time                  reset_in
5h                    99.8 2026-08-20T08:02:31Z        32 minutes
weekly                99.8 2026-08-26T18:35:04Z        6 days, 11 hours

5h 窗口用于平滑全球容量；weekly 与你的订阅档位挂钩
```

## CLI 命令

| 命令 | 说明 |
|---|---|
| `sub-agy run --plan <plan.md>` | 派发计划，返回 job_id |
| `sub-agy status [--all]` | 查看作业状态 |
| `sub-agy result <job-id>` | 收割已完成作业的结果 |
| `sub-agy feedback <job-id> "..."` | 提交反馈，启动下一轮修复 |
| `sub-agy watch <job-id>` | 原地等待作业进入终态 |
| `sub-agy cancel <job-id>` | 取消作业 |
| `sub-agy list` | 列出当前项目下的作业 |
| `sub-agy cleanup <job-id>` | 清理作业目录与分支 |
| `sub-agy quota [--pretty]` | 查询 Antigravity 额度（0 token） |
| `sub-agy doctor` | 环境诊断 |

## 安全与合规

- **纯官方 CLI 进程编排**：sub-agy 不接触任何模型 API，不存储、不中转 API key。
- **默认沙箱**：agy 默认以 `--sandbox` 运行，工具调用需逐条批准；无人值守时推荐在 agy 侧配置白名单，或使用显式 `--auto-approve`。
- **自动打回、人工合并**：`feedback` 自动保留上下文并重新执行；`git merge agy/<job-id>` 永远由用户手动触发。

## License

[MIT](./LICENSE)

设计文档见 [SPEC.md](./SPEC.md)。
