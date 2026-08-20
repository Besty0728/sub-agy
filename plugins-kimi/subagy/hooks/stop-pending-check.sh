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

# 检查 cwd/.subagy/jobs 是否存在
if [ ! -d "$CWD/.subagy/jobs" ]; then
    exit 0
fi

# 调用 sub-agy pending，获取待收割作业列表
PENDING_OUTPUT=$(sub-agy pending --cwd "$CWD" 2>/dev/null || echo "")

# 若命令不存在、失败、或输出垃圾，fail-open（exit 0）
if [ -z "$PENDING_OUTPUT" ]; then
    exit 0
fi

# 尝试解析 JSON
PENDING_JOBS=$(echo "$PENDING_OUTPUT" | python3 -c "import sys, json; jobs = json.load(sys.stdin); print(','.join([j['job_id'] for j in jobs]) if jobs else '')" 2>/dev/null || echo "")

# 输出为空（[] 或解析失败）→ exit 0
if [ -z "$PENDING_JOBS" ]; then
    exit 0
fi

# 非空：stderr 输出警告并 exit 2
echo "sub-agy 作业 $PENDING_JOBS 已完成未收割，请执行 /subagy:harvest $PENDING_JOBS 进行验收" >&2
exit 2
