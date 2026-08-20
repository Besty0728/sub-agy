"""Result contract schema constants."""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "tests_ran": {"type": "array", "items": {"type": "string"}},
        "tests_passed": {"type": "boolean"},
        "acceptance_met": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "followups": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "files_changed", "tests_passed"],
    "additionalProperties": False,
}

META_STATES = ("queued", "running", "done", "error", "cancelled", "interrupted")

AGY_STATUSES = (
    "SUCCESS",
    "ERROR",
    "CANCELED",
    "INTERRUPTED",
    "INVALID",
    "WAITING",
    "RUNNING",
)

EXIT_CODES = {
    "success": 0,
    "generic_error": 1,
    "not_found": 3,
    "not_finished": 4,
    "concurrency": 5,
    "not_git": 6,
    "timeout": 124,
    "usage": 64,
    "not_installed": 127,
}
