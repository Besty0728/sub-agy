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


def fmt_tokens(n: int | None) -> str:
    """Format token count into human-readable representation: 448266 -> '448.3k', 1234567 -> '1.2M', None -> '-'."""
    if n is None:
        return "-"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def fmt_elapsed(seconds: float | None) -> str:
    """Format elapsed seconds into 'm:ss' or 'h:mm:ss': 65.2 -> '1:05', 3725 -> '1:02:05', None -> '-'."""
    if seconds is None:
        return "-"
    total_seconds = int(round(seconds))
    if total_seconds < 0:
        total_seconds = 0
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def extract_tokens(usage: object) -> dict[str, int | None] | None:
    """Extract input, output, and total tokens from usage dict."""
    if not isinstance(usage, dict):
        return None
    return {
        "input": usage.get("input_tokens"),
        "output": usage.get("output_tokens"),
        "total": usage.get("total_tokens"),
    }

