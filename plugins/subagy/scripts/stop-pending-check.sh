#!/bin/bash
set -euo pipefail

# 读取 stdin 并解析 JSON，检查是否有未收割的 sub-agy 作业
# 如果有，阻断 Stop，否则允许继续

# 读取 stdin JSON
stdin_json=$(cat)

# 检查 stop_hook_active 是否为 true，若是则不做任何事（防循环）
stop_hook_active=$(echo "$stdin_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('stop_hook_active', False))" 2>/dev/null || echo "false")
if [ "$stop_hook_active" = "True" ] || [ "$stop_hook_active" = "true" ]; then
  exit 0
fi

# 获取 cwd，优先 stdin 的 cwd 字段，其次 $CLAUDE_PROJECT_DIR，再其次 $PWD
cwd=$(echo "$stdin_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd', ''))" 2>/dev/null || echo "")
if [ -z "$cwd" ]; then
  cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
fi

# §19.1: 快速前置判断改为「$cwd/.subagy/jobs 不存在 且 注册表文件不存在 → exit 0」
registry_file="${HOME}/.config/sub-agy/recent_projects.json"
if [ ! -d "$cwd/.subagy/jobs" ] && [ ! -f "$registry_file" ]; then
  exit 0
fi

# 调用 sub-agy pending --under 获取未收割的作业列表
# 任何错误或非 JSON 输出都 fail-open（exit 0）
pending_json=$(bash "$(dirname "$0")/ab" pending --under "$cwd" 2>/dev/null || echo "[]")

# 校验 JSON 格式，如果不是有效 JSON，fail-open
if ! echo "$pending_json" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  exit 0
fi

# 检查是否为空数组
if [ "$pending_json" = "[]" ]; then
  exit 0
fi

# 非空，提取 job_id 列表并构建阻断消息
# §19.1: 若元素含 project 字段，按 "job_id(project)" 形式列出
job_list=$(echo "$pending_json" | python3 -c "
import sys, json
try:
  data = json.load(sys.stdin)
  if isinstance(data, list) and len(data) > 0:
    items = []
    for item in data:
      job_id = item.get('job_id', '')
      project = item.get('project')
      if project:
        items.append(f'{job_id}({project})')
      else:
        items.append(job_id)
    print(', '.join(items))
  else:
    print('')
except:
  print('')
" 2>/dev/null || echo "")

# 如果成功提取了 job_id，向 stdout 输出阻断信息
if [ -n "$job_list" ]; then
  echo "{\"decision\":\"block\",\"reason\":\"sub-agy 作业 $job_list 已完成未收割，请按 /subagy:harvest 流程收割\"}"
fi

exit 0
