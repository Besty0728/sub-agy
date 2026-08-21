# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

**sub-agy** 将 Antigravity CLI（`agy`，Gemini 模型）变成规划侧 agent 的异步作业执行后端。纯进程编排，不碰 API key，不做网络请求。工作流：规划 agent 调用 `sub-agy run --plan <plan.md>`，立即返回 job_id；detached supervisor 子进程在后台托管 agy 执行，即使调用方退出也继续运行；完成时 watcher 唤醒 agent 做验收。

## 快速命令

```bash
# 安装 CLI（无需 clone）
uv tool install git+https://github.com/Besty0728/sub-agy

# 环境诊断
sub-agy doctor

# 测试（所有单测用 fake agy fixture，不依赖真实 agy）
uv run pytest

# 单个测试（例）
uv run pytest tests/test_queue.py -k <name>

# 真实冒烟测试（需真实 agy）
scripts/smoke.sh

# 配额查询（0 token）
sub-agy quota --oneline
```

## 核心架构

### 磁盘状态与不变式
- CLI 完全无状态；所有状态在 `.subagy/` 下：`jobs/<job_id>/meta.json`（唯一事实源）、`events.ndjson`（stream-json 日志）、`result.json`（结构化验收数据）
- **终态不变式**：先原子写 `result.json`，再翻转 `meta.json` 的 state 字段到终态（done/error/cancelled/interrupted），防止意外丢失结果

### Detached supervisor 生命周期
- `run` 或 `feedback` 立即 fork detached `_supervise` 子进程，返回调用方
- supervisor 进程内：(1) 调用 `acquire_slot()` 在 `queue.lock` 上 flock，等待槽位释放；(2) 更新 meta state → "running"；(3) spawn `agy` 进程，逐行解析 stream-json 输出；(4) 若 agy stdout 为空（已知 bug），兜底读 `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`（仅当 brain 目录能唯一关联本作业才恢复）；(5) 写结果，翻转 state
- agy 总是以 `--dangerously-skip-permissions` 启动，安全边界 = worktree 隔离 + plan scope/constraints + 人工 merge

### 并发与排队
- `queue.lock` 用 flock 序列化槽位会计（queue.py）
- FIFO 排队：每个 queued 的 job 通过 `_fifo_key()`（queued_at + job_id）排序，supervisor 在 acquire_slot 中等待前面的 job 完成
- 超时：等待槽位 > `queue_timeout` 则作业失败；每个作业执行 > `default_timeout` 则超时

### Git worktree 隔离
- 每个作业独占一个 worktree，分支名 `agy/<job_id>`
- `add_worktree()` 创建、`remove_worktree()` 清理；main 分支始终干净
- 人工 merge：`git merge agy/<job_id>` 必须由用户触发（不自动化）

### 三端插件与 fake agy 测试
- 插件前端（纯 markdown/脚本，无构建）：`plugins/subagy`（Claude Code）、`plugins-codex/subagy`（Codex 技能）、`plugins-kimi/subagy`（Kimi Code 后台 watcher subagent，manifest 在仓库根 `.kimi-plugin/plugin.json`）
- 所有插件最终 shell out 到 `sub-agy` 二进制；无 CLI 则 exit 127
- 测试：`tests/conftest.py` 的 `fake_agy` fixture 创建 shell 脚本，环境变量（`FAKE_AGY_STATUS`、`FAKE_AGY_RESPONSE` 等）控制输出

## 版本与配置

**版本锚点**（发布时须同步 6 处 + uv.lock）：
- pyproject.toml 中 `version = "0.1.1"`
- src/sub_agy/__init__.py 中 `__version__ = "0.1.1"`
- plugins/subagy/.claude-plugin/plugin.json 中 version
- plugins-codex/subagy/.codex-plugin/plugin.json 中 version
- .kimi-plugin/plugin.json 中 version
- .claude-plugin/marketplace.json 中 plugins[0].version

自定义命令 `/updateversion` 和 `/release` 负责版本同步（见 .claude/commands/）。

**配置文件**：`~/.config/sub-agy/config.toml`（可选）；所有键都可用 CLI 标志覆盖。

## 开发要点

1. **续会话安全**：agy 命令必须用 `--conversation <id>` 保留上下文，禁止用 `--continue`
2. **进程编排不涉及模型 API**：所有 API key 始终不接触，通过 agy 二进制完全隔离
3. **单测覆盖**：所有涉及 agy 的测试必须用 fake agy fixture，环境变量控制行为，无真实依赖
