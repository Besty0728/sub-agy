#!/bin/bash
set -euo pipefail

# 读 stdin JSON，解析 cwd 字段
PAYLOAD=$(cat)

CWD=$(echo "$PAYLOAD" | python3 -c "import sys, json; print(json.load(sys.stdin).get('cwd', '.'))" 2>/dev/null || echo ".")

# 防循环：若 payload 中有 stop_hook_active=true，直接退出
STOP_HOOK_ACTIVE=$(echo "$PAYLOAD" | python3 -c "import sys, json; print(json.load(sys.stdin).get('stop_hook_active', False))" 2>/dev/null || echo "false")
if [ "$STOP_HOOK_ACTIVE" = "True" ] || [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    exit 0
fi

# §19.1: 快速前置判断改为「$CWD/.subagy/jobs 不存在 且 注册表文件不存在 → exit 0」
REGISTRY_FILE="${HOME}/.config/sub-agy/recent_projects.json"
if [ ! -d "$CWD/.subagy/jobs" ] && [ ! -f "$REGISTRY_FILE" ]; then
    exit 0
fi

# 调用 sub-agy pending --under，获取待收割作业列表
PENDING_OUTPUT=$(sub-agy pending --under "$CWD" 2>/dev/null || echo "")

# 若命令不存在、失败、或输出垃圾，fail-open（exit 0）
if [ -z "$PENDING_OUTPUT" ]; then
    exit 0
fi

# 尝试解析 JSON，构建 job_id 列表
# §19.1: 若元素含 project 字段，按 "job_id(project)" 形式列出
PENDING_JOBS=$(echo "$PENDING_OUTPUT" | python3 -c "
import sys, json
try:
    jobs = json.load(sys.stdin)
    if jobs:
        items = []
        for j in jobs:
            job_id = j.get('job_id', '')
            project = j.get('project')
            if project:
                items.append(f'{job_id}({project})')
            else:
                items.append(job_id)
        print(','.join(items))
    else:
        print('')
except:
    print('')
" 2>/dev/null || echo "")

# 输出为空（[] 或解析失败）→ exit 0
if [ -z "$PENDING_JOBS" ]; then
    exit 0
fi

# 非空：stderr 输出警告并 exit 2
echo "sub-agy 作业 $PENDING_JOBS 已完成未收割，请执行 /subagy:harvest $PENDING_JOBS 进行验收" >&2
exit 2
