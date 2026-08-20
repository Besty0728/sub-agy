---
description: 检查 sub-agy 运行环境。触发词：诊断、doctor、检查环境、检查依赖
---

## 第一步：运行 doctor 诊断

执行以下命令来检查环境：

```bash
sub-agy doctor --pretty
```

将输出**逐字**贴进回复。

## 如果 doctor 命令不存在

如果上面的命令返回 exit code 127 或 "command not found"，说明 sub-agy CLI 未安装。继续以下流程：

### 检查 uv 是否已安装

```bash
command -v uv && echo "uv found" || echo "uv not found"
```

- 如果输出 "uv found"，跳转到【有 uv 的情况】
- 如果输出 "uv not found"，跳转到【无 uv 的情况】

### 有 uv 的情况

向用户提问且仅问一次：

「您希望现在安装 sub-agy CLI 吗？（推荐）」

根据用户回复：

**如果用户同意（任何肯定的表示如"好的"、"是"、"可以"等）**，执行：

```bash
uv tool install git+https://github.com/Besty0728/sub-agy
```

等待完成后，重新运行：

```bash
sub-agy doctor --pretty
```

将新的输出**逐字**贴进回复。

**如果用户拒绝**，告知用户可以稍后手动运行以上命令，或访问 sub-agy 官方文档了解安装方法，然后结束。

### 无 uv 的情况

向用户提问且仅问一次：

「您需要安装 uv（Python 包与工具管理器），然后再安装 sub-agy。是否现在同时安装两者？」

根据用户回复：

**如果用户同意**，依次执行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

等待完成，然后：

```bash
uv tool install git+https://github.com/Besty0728/sub-agy
```

完成后，重新运行：

```bash
sub-agy doctor --pretty
```

将新的输出**逐字**贴进回复。

**如果用户拒绝**，提供以下手动安装指引后结束：

```
1. 访问 https://github.com/astral-sh/uv 下载并安装 uv
2. 安装完成后，运行：uv tool install git+https://github.com/Besty0728/sub-agy
3. 然后重新运行 doctor 诊断
```

## 如果 doctor 输出中有问题项

如果 doctor 输出显示有 `issues` 列表且非空，会同时包含 `hints` 修复建议。请**逐字转述**这些建议给用户：

> 修复建议：
> • [每条 hint 逐一列出]

特别地：
- **agy 安装问题**（提示含"安装 Antigravity CLI"）：指引用户去官方安装 agy，然后裸跑一次 `agy` 交互完成 OAuth 登录，sub-agy 不代办登录
- **agy 版本过低**（提示含"升级 agy"）：指引用户更新 agy
- **认证缺失**（提示含"登录"）：指引用户运行裸 `agy` 交互登录
- **config 文件问题**（提示含"config.toml"）：指引用户检查配置文件

## 铁律

- ✅ 白名单命令（需要一次确认后执行）：
  - `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - `uv tool install git+https://github.com/Besty0728/sub-agy`

- ❌ 禁止代办：
  - Antigravity CLI (agy) 本体安装
  - OAuth 登录流程

- 任何安装命令**必须先经恰好一次用户确认**
- 向用户提问**恰好一次**，不重复问同一个问题
