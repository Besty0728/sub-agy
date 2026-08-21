# Update Version — 安全更新项目版本 + 生成 CHANGELOG

你是 sub-agy 项目的版本更新助手。此命令只负责准备一个可发布的版本候选；它不会创建 tag 或 GitHub Release。

版本更新通过 GitHub `releases/latest` 的稳定 Release 向使用者传递。只有正式发布新的非 draft、非 prerelease GitHub Release 后，`releases/latest` 才会反映新版本。

## 输入

用户必须提供不带 `v` 的稳定语义版本号，例如 `/updateversion 0.2.0`。

- 只接受严格的 `MAJOR.MINOR.PATCH`。
- 不接受 `v0.2.0`、`0.2`、`0.2.0-beta.1` 或四段版本号。
- 未提供或格式不合法时停止，并提示：`/updateversion <新版本号>`。

## 步骤 1：远端与版本线预检查

1. 确认当前在 `main` 分支；不在则停止。
2. 确认 GitHub CLI 已登录：
   ```bash
   gh auth status
   ```
3. 获取最新远端引用。任何 fetch 失败都必须停止，不能用过期的本地引用继续判断：
   ```bash
   git fetch origin main --tags
   ```
4. 检查工作区：
   ```bash
   git status --porcelain
   ```
   有未提交改动时明确警告，但不阻止，因为这些改动可能正是本次待发布内容。后续只修改约定的版本锚点，不覆盖其他改动。
5. 确认 `main` 没有遗漏远端提交：
   ```bash
   git log main..origin/main --oneline
   ```
   有输出即停止：本地 `main` 落后于远端，必须先同步。
6. 从 `pyproject.toml` 读取当前版本 `{OLD_VER}`：
   ```bash
   grep 'version = ' pyproject.toml | head -1
   ```
7. 查询 GitHub 当前最新稳定 Release：
   ```bash
   gh api repos/Besty0728/sub-agy/releases/latest \
     --jq '{tag_name,html_url,draft,prerelease}'
   ```
   - `tag_name` 必须严格为 `vMAJOR.MINOR.PATCH`。
   - 必须是非 draft、非 prerelease。
   - 正常情况下其版本应等于 `{OLD_VER}`；若不一致，停止并报告"main 当前版本与 latest Release 已错位"。
   - 首个 release 之前 `gh api releases/latest` 返回 404，这是正常的，注明允许进行。
8. 用数值元组比较语义版本。`{NEW_VER}` 必须严格高于 `{OLD_VER}`；禁止使用字符串字典序比较。

## 步骤 2：版本号占用检查

同时检查本地 tag、远端 tag 和 GitHub Release；任一已存在都停止，版本号不可复用：

```bash
git tag -l "v{NEW_VER}"
git ls-remote --tags origin "refs/tags/v{NEW_VER}" "refs/tags/v{NEW_VER}^{}"
gh release view "v{NEW_VER}"
```

- `gh release view` 返回"未找到"才表示该检查通过。
- 不得删除、移动或强制重推已发布 tag。
- 若版本曾经创建过 tag 但尚未创建 Release，同样视为已占用；修复后应换用新的 patch 版本。

## 步骤 3：分析 main 相对稳定版的变更

统一以 `origin/main` 为稳定基线。若本地仍有未 push 的提交，从 `main` 开始分析；否则从 `origin/main` 开始：

```bash
git log origin/main..HEAD --oneline
git diff origin/main --stat
git status --short
```

如果既没有提交差异，也没有实质性工作区差异，停止并询问用户是否确实要创建仅含版本号的空版本。

对关键功能文件查看具体 diff，重点关注：

- `src/sub_agy/**/*.py`
- `plugins/**/*.md` 与 `plugins-codex/**/*.md`
- `.claude-plugin/**` 与 `.codex-plugin/**` 配置变化
- 用户可感知的 README 与安装兼容性变化

基于 commit message 与实际 diff 组织 CHANGELOG：

- **Added**：新增功能、新参数、新 CLI 选项。
- **Changed**：行为、API、工作流或文档契约变化。
- **Fixed**：缺陷修复。
- **Docs**：纯文档变化，可选。

每条使用中文，以 `**粗体标题** — 描述` 编写；描述具体但不堆砌实现细节。

## 步骤 4：只更新明确的项目版本锚点

使用精确的文本替换，修改以下 6 处锚点。禁止宽泛替换：

| 文件 | 唯一允许修改的版本锚点 |
|------|------------------------|
| `pyproject.toml` | `version = "{OLD_VER}"` |
| `src/sub_agy/__init__.py` | `__version__ = "{OLD_VER}"` |
| `plugins/subagy/.claude-plugin/plugin.json` | `"version": "{OLD_VER}"` |
| `plugins-codex/subagy/.codex-plugin/plugin.json` | `"version": "{OLD_VER}"` |
| `.kimi-plugin/plugin.json` | `"version": "{OLD_VER}"` |
| `.claude-plugin/marketplace.json` | `plugins[0]` 的 `"version": "{OLD_VER}"` |

CHANGELOG 处理：

1. 若 `CHANGELOG.md` 不存在，按 Keep a Changelog 风格自举创建，包含头部链接定义。
2. 在 CHANGELOG 最顶部插入新版本条目 `## [{NEW_VER}] - {TODAY}`（YYYY-MM-DD 格式）。
3. 在该条目下添加本次变更内容（Added/Changed/Fixed/Docs 组织）。

### 禁止宽泛替换

- 不得在 README 或整个仓库里把所有 `{OLD_VER}` 替换成 `{NEW_VER}`。
- `README.md` 第 46 行附近的 `Since v0.1.1` 与 `README-CN.md` 对应位置的 `v0.1.1 起` 是版本史记述，绝不随升版改动。
- `.agents/plugins/marketplace.json` **不动它**（无 version 字段）。
- git 历史与已发布 tag 中的旧版本号一律不动。
- 测试数据或兼容性说明中的相同数字是跨版本引用，不机械修改。

## 步骤 5：机器可验证的一致性检查

依次执行以下验证：

```bash
# 检查 pyproject.toml
grep "^version = \"${NEW_VER}\"" pyproject.toml

# 检查 __init__.py
grep "__version__ = \"${NEW_VER}\"" src/sub_agy/__init__.py

# 检查三个 plugin.json（使用 python json 校验）
python3 -m json.tool plugins/subagy/.claude-plugin/plugin.json >/dev/null
python3 -m json.tool plugins-codex/subagy/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool .kimi-plugin/plugin.json >/dev/null

# 用 python 逐个校验版本字段（heredoc 不加引号，让 ${NEW_VER} 展开）
python3 << EOF
import json
files = [
  'plugins/subagy/.claude-plugin/plugin.json',
  'plugins-codex/subagy/.codex-plugin/plugin.json',
  '.kimi-plugin/plugin.json'
]
for f in files:
    with open(f) as fh:
        data = json.load(fh)
        assert data.get('version') == "${NEW_VER}", f + ' version mismatch'
print("Plugin versions OK")
EOF

# 检查 marketplace.json 的 plugins[0].version
python3 << EOF
import json
with open('.claude-plugin/marketplace.json') as f:
    data = json.load(f)
    assert data['plugins'][0]['version'] == "${NEW_VER}", 'marketplace version mismatch'
print("Marketplace version OK")
EOF

# 检查 CHANGELOG 顶部
grep -c "## \[${NEW_VER}\]" CHANGELOG.md | grep -q "^1$"

# 锁定依赖
uv lock

# 检查 uv.lock 中 sub-agy 的 version
grep -A2 'name = "sub-agy"' uv.lock | grep "version = \"${NEW_VER}\""

# 运行全量测试
uv run pytest
```

另外确认：

1. `CHANGELOG.md` 顶部最新版本恰好为 `{NEW_VER}`，且只新增一个该版本标题。
2. 没有创建 `v{NEW_VER}` tag，也没有 GitHub Release。
3. `git diff` 只改动预期文件（6 处锚点 + CHANGELOG + uv.lock），没有第三方版本文档被误替换。
4. `uv run pytest` 全绿。

验证标准是"所有当前版本锚点都等于新版本"，不是"旧版本号在仓库中完全消失"。旧版本作为历史记录或兼容性边界存在是正确的。

## 步骤 6：输出交接摘要

输出必须明确区分"版本候选已准备"和"版本已发布"：

```text
✅ 版本候选已从 {OLD_VER} 更新到 {NEW_VER}
✅ 项目版本锚点一致性检查通过
✅ 本地 / 远端 tag 与 GitHub Release 均未占用 v{NEW_VER}
✅ uv run pytest 全绿

当前尚未发布版本。
只有 /release 完成验证、测试和 Release 创建后，
releases/latest 才会变为 v{NEW_VER}。

请审阅后提交：
git add pyproject.toml src/sub_agy/__init__.py \
  plugins/subagy/.claude-plugin/plugin.json \
  plugins-codex/subagy/.codex-plugin/plugin.json \
  .kimi-plugin/plugin.json .claude-plugin/marketplace.json \
  CHANGELOG.md uv.lock
git commit -m "chore: bump version to {NEW_VER}"
```

同时列出所有修改文件，并完整展示新增的 CHANGELOG 条目。

## 注意事项

- 不自动执行 `git commit`、`git tag`、`git push` 或 `gh release create`。
- 不把"plugin.json 已更新""tag 已创建"和"版本已发布"混为一谈。
- 版本发布的唯一远端来源是 GitHub 最新稳定 Release，而不是最新 commit 或孤立 tag。
