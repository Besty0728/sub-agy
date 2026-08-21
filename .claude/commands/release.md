# Release — 候选验证 → main 推送 → 正式 Release

你是 sub-agy 项目的正式发布助手。发布必须遵守以下顺序：

```text
版本一致性检查
  → Release Note 生成与审阅
  → 本地全量测试门禁
  → 用户明确确认正式发布
  → 推送 main
  → 创建并推送 annotated tag
  → 创建稳定 GitHub Release
  → 核验 releases/latest 与发布页面
```

用户更新通知只读取 GitHub `releases/latest`。因此在最后一步正式 Release 创建成功之前，不能宣称版本已发布。

## 输入

用户可提供不带 `v` 的版本号，例如 `/release 0.2.0`；未提供时从 `CHANGELOG.md` 顶部最新的 `## [x.y.z]` 解析。

- 版本必须严格为 `MAJOR.MINOR.PATCH`。
- tag 固定为 `v{VERSION}`。

## 阶段 1：只读预检查

1. 确认当前在 `main` 分支。
2. 工作区必须完全干净：
   ```bash
   git status --porcelain
   ```
   有任何修改或未跟踪文件都停止，提示先提交或处理。
3. 检查 GitHub CLI 登录状态并获取最新远端引用：
   ```bash
   gh auth status
   git fetch origin main --tags
   ```
   任一失败都停止。
4. 将当前提交固定为变量并在整个流程中保持不变：
   ```bash
   RELEASE_SHA=$(git rev-parse main)
   VERSION_TAG="v{VERSION}"
   ```
5. 确认本地 `main` 没有遗漏远端提交：
   ```bash
   git log main..origin/main --oneline
   ```
   有输出即停止，说明本地落后。
6. 验证全部 6 处版本锚点与 CHANGELOG 顶部版本一致：
   ```bash
   grep "^version = \"${VERSION}\"" pyproject.toml
   grep "__version__ = \"${VERSION}\"" src/sub_agy/__init__.py
   grep "## \[${VERSION}\]" CHANGELOG.md
   grep -A2 'name = "sub-agy"' uv.lock | grep "version = \"${VERSION}\""

   python3 -c '
   import json, sys
   ver = sys.argv[1]
   for f in ["plugins/subagy/.claude-plugin/plugin.json",
             "plugins-codex/subagy/.codex-plugin/plugin.json",
             ".kimi-plugin/plugin.json"]:
       assert json.load(open(f)).get("version") == ver, f + " version mismatch"
   mp = json.load(open(".claude-plugin/marketplace.json"))
   assert mp["plugins"][0]["version"] == ver, "marketplace version mismatch"
   print("All version anchors OK")
   ' "${VERSION}"
   ```
   任一不一致即停止。
7. 查询当前 GitHub 最新稳定 Release。该版本必须严格低于候选版本：
   ```bash
   gh api repos/Besty0728/sub-agy/releases/latest \
     --jq '{tag_name,html_url,draft,prerelease}'
   ```
   - 首个 release 之前 `gh api releases/latest` 返回 404，这是正常的，允许继续。
8. 同时确认本地 tag、远端 tag、GitHub Release 都不存在：
   ```bash
   git tag -l "${VERSION_TAG}"
   git ls-remote --tags origin "refs/tags/${VERSION_TAG}" "refs/tags/${VERSION_TAG}^{}"
   gh release view "${VERSION_TAG}"
   ```
   任一已存在都停止。绝不删除、移动或强制重推已发布 tag。

## 阶段 2：生成并审阅 Release Note

从 CHANGELOG 当前版本条目生成 `.releases/v{VERSION}.md`。该目录由 `.gitignore` 忽略，不会被提交。

格式：

```markdown
# v{VERSION} — {一句话总结，提炼 2-4 个核心特性或修复}

## ⭐ Highlights

- **{特性标题}**：{用户能获得的能力或修复}

## Added

{从 CHANGELOG Added 提炼}

## Changed

{从 CHANGELOG Changed 提炼}

## Fixed

{存在时加入}

## Docs

{存在时加入}

### 完整更改日志见 https://github.com/Besty0728/sub-agy/blob/main/CHANGELOG.md
```

Highlights 使用用户语言，突出"能做什么"，不要照搬内部实现。

显示完整 Release Note 给用户审阅。正式发布确认之前可以继续修改该临时文件，但不得修改已固定的 `RELEASE_SHA` 对应源码；若源码发生变化，必须从阶段 1 重新开始。

## 阶段 3：本地测试门禁

替代外部 CI 矩阵的唯一手段，必须在本地通过：

```bash
uv run pytest
```

必须全绿。失败即停止，不能继续；修复后生成新提交，回到阶段 1 重新审计。

## 阶段 4：正式发布确认闸门

在执行任何 main 推送或 tag 创建前，向用户展示：

- `{VERSION}` 与 `RELEASE_SHA`
- Release Note 文件和完整内容
- 本地 `uv run pytest` 的成功结果
- 接下来会创建不可复用的 `v{VERSION}` tag 并在检查成功后创建正式 GitHub Release

必须获得用户明确的"确认正式发布"后才能继续。普通的"看看""准备一下""生成说明"不构成授权。

## 阶段 5：安全验证与 main 同步

用户确认可能跨越一个对话回合，不能假设之前的 shell 变量或远端状态仍然有效。继续前重新建立并核验固定值：

```bash
RELEASE_SHA="{阶段 4 展示并获确认的完整 SHA}"
VERSION_TAG="v{VERSION}"

git fetch origin main --tags
test -z "$(git status --porcelain)"
test "$(git branch --show-current)" = "main"
test "$(git rev-parse main)" = "${RELEASE_SHA}"
test -z "$(git log main..origin/main --oneline)"
test -z "$(git tag -l "${VERSION_TAG}")"
test -z "$(git ls-remote --tags origin "refs/tags/${VERSION_TAG}" "refs/tags/${VERSION_TAG}^{}")"
```

所有条件都必须满足。任一变化都使之前的确认失效，回到阶段 1 重新审计。
（此时本地 `main` 允许领先 `origin/main`——版本候选提交通常尚未推送；只禁止落后。）

核验通过后推送 main，并验证远端精确一致：

```bash
git push origin main
git fetch origin main
test "$(git rev-parse origin/main)" = "${RELEASE_SHA}"
```

推送失败或验证不一致即停止，不得继续打 tag。

## 阶段 6：创建并验证 annotated tag

tag 必须显式打在已验证的 `RELEASE_SHA` 上：

```bash
git tag -a "${VERSION_TAG}" "${RELEASE_SHA}" -m "${VERSION_TAG}"
git push origin "refs/tags/${VERSION_TAG}"
```

本地验证：

```bash
test "$(git rev-parse "${VERSION_TAG}^{}")" = "${RELEASE_SHA}"
git merge-base --is-ancestor "${VERSION_TAG}^{}" origin/main
```

远端验证必须展开 annotated tag，不能只比较 tag 对象 SHA。执行以下 API 调用确认 tag 指向正确的提交：

```bash
TAG_OBJECT=$(gh api \
  "repos/Besty0728/sub-agy/git/ref/tags/${VERSION_TAG}" \
  --jq '.object.sha')
TAG_TYPE=$(gh api \
  "repos/Besty0728/sub-agy/git/ref/tags/${VERSION_TAG}" \
  --jq '.object.type')
test "${TAG_TYPE}" = "tag"
TAG_COMMIT=$(gh api \
  "repos/Besty0728/sub-agy/git/tags/${TAG_OBJECT}" \
  --jq '.object.sha')
test "${TAG_COMMIT}" = "${RELEASE_SHA}"
```

若本地 tag 已创建但 push 因网络错误失败，只有在远端仍不存在同名 tag、且本地 `v{VERSION}^{}` 精确等于 `RELEASE_SHA` 时，才可重试同一条 push；不得重建或移动它。

## 阶段 7：创建正式稳定 GitHub Release

tag 验证成功后，使用已经存在且已验证的 tag 创建 Release：

```bash
gh release create "${VERSION_TAG}" \
  --verify-tag \
  --latest \
  --title "${VERSION_TAG}" \
  --notes-file ".releases/${VERSION_TAG}.md"
```

不得使用 `--prerelease` 或 `--draft`。不得传 `--target`，因为 tag 已显式创建并验证。

若命令因网络中断返回不确定结果，先执行 `gh release view "${VERSION_TAG}"` 判断 Release 是否已经创建，再决定是否重试，不得盲目重复发布操作。

## 阶段 8：核验更新提醒真实数据源

GitHub API 可能有短暂传播延迟；在有限时间内轮询：

```bash
gh api repos/Besty0728/sub-agy/releases/latest \
  --jq '{tag_name,html_url,draft,prerelease}'
```

最终必须同时满足：

- `tag_name == v{VERSION}`
- `draft == false`
- `prerelease == false`
- `html_url == https://github.com/Besty0728/sub-agy/releases/tag/v{VERSION}`
- 远端 tag 展开后的 commit 等于 `RELEASE_SHA`
- `main` 与 `origin/main` 均等于 `RELEASE_SHA`

只有全部满足后，才能报告：稳定版更新通知现在会把旧版本用户引导到这个 Release 页面。

## 最终输出

输出：

- 正式 Release URL
- `VERSION_TAG` 与 `RELEASE_SHA`
- 本地 pytest 成功结果与耗时
- Release Note URL
- main/origin/main 一致性结果
- `releases/latest` 核验结果
- 当前分支与工作区状态

## 不可违反的规则

- 未得到明确正式发布确认，不推送 main、不创建 tag、不创建 Release。
- 不移动、不删除、不强制重推已发布 tag。
- 不跳过本地 `uv run pytest`，也不拿旧的绿色结果代替。
- 不在 pytest 失败时先发布 Release。
- 版本号不可复用。若发布失败，修复后必须使用新的 patch 版本重新走完整流程。
