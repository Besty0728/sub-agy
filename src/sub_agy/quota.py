"""Quota query: parse Antigravity usage envelope into normalized groups."""

from __future__ import annotations

import json
import re
import subprocess
from collections import OrderedDict

from .config import Config, agy_bin_path

_REFRESH_RE = re.compile(r"refresh in (.+?)\.?$", re.IGNORECASE)

_WEEKLY_NAMES = {"weekly limit remaining", "weekly"}
_FIVE_HOUR_NAMES = {"five hour limit remaining", "five hour", "5h"}

_GROUP_NAME_ZH = {
    "Gemini Models": "Gemini 模型",
    "Claude and GPT models": "Claude/GPT 模型",
}


def _humanize_reset_zh(reset_in: str | None) -> str | None:
    """Convert English reset_in string to compact Chinese.

    Examples:
        '6 days, 9 hours' -> '6天9小时'
        '4 hours, 28 minutes' -> '4小时28分'
        '32 minutes' -> '32分钟'
    """
    if not reset_in or reset_in.strip() in ("", "-"):
        return None

    text = reset_in.strip()
    day_match = re.search(r"(\d+)\s*days?", text, re.IGNORECASE)
    hour_match = re.search(r"(\d+)\s*hours?", text, re.IGNORECASE)
    min_match = re.search(r"(\d+)\s*minutes?", text, re.IGNORECASE)
    sec_match = re.search(r"(\d+)\s*seconds?", text, re.IGNORECASE)

    parts: list[str] = []
    if day_match:
        parts.append(f"{day_match.group(1)}天")
    if hour_match:
        parts.append(f"{hour_match.group(1)}小时")
    if min_match:
        m_val = min_match.group(1)
        if day_match or hour_match:
            parts.append(f"{m_val}分")
        else:
            parts.append(f"{m_val}分钟")
    if sec_match:
        parts.append(f"{sec_match.group(1)}秒")

    if not parts:
        return None
    return "".join(parts)


def format_oneline(data: dict) -> str:
    """Format normalized quota data into a compact Chinese sentence."""
    groups = data.get("groups", [])
    if not groups:
        return ""

    group_sentences: list[str] = []
    for group in groups:
        raw_name = group.get("name", "Unknown")
        group_name = _GROUP_NAME_ZH.get(raw_name, raw_name)

        buckets_by_window: dict[str, dict] = {}
        for bucket in group.get("buckets", []):
            window = bucket.get("window")
            if window and window not in buckets_by_window:
                buckets_by_window[window] = bucket

        clauses: list[str] = []
        for target_window, label in [("5h", "5h"), ("weekly", "7d")]:
            bucket = buckets_by_window.get(target_window)
            if not bucket:
                continue
            pct = bucket.get("remaining_pct", 0.0)
            reset_zh = _humanize_reset_zh(bucket.get("reset_in"))
            if reset_zh:
                clauses.append(f"{label} 限额剩余 {pct:.1f}%（{reset_zh}后重置）")
            else:
                clauses.append(f"{label} 限额剩余 {pct:.1f}%")

        if clauses:
            group_sentences.append(f"{group_name}：{'，'.join(clauses)}")

    return "；".join(group_sentences)



def _window_from_text(name: str) -> str:
    lowered = name.strip().lower()
    if lowered in _WEEKLY_NAMES:
        return "weekly"
    if lowered in _FIVE_HOUR_NAMES:
        return "5h"
    # Fallback heuristic: if it contains "week" or "hour".
    if "week" in lowered:
        return "weekly"
    if "hour" in lowered or "5h" in lowered:
        return "5h"
    return lowered


def _extract_reset_in(description: str | None) -> str | None:
    if not description:
        return None
    match = _REFRESH_RE.search(description)
    if not match:
        return None
    return match.group(1).strip()


def _normalize_structured(envelope: dict) -> dict:
    raw_groups = envelope.get("command", {}).get("data", {}).get("groups", [])
    groups = []
    for raw_group in raw_groups:
        buckets = []
        for raw_bucket in raw_group.get("buckets", []):
            fraction = raw_bucket.get("remaining_fraction")
            if fraction is None:
                continue
            try:
                remaining_pct = round(float(fraction) * 100, 1)
            except (TypeError, ValueError):
                continue
            buckets.append(
                {
                    "window": raw_bucket.get("window", "unknown"),
                    "remaining_pct": remaining_pct,
                    "reset_time": raw_bucket.get("reset_time"),
                    "reset_in": _extract_reset_in(raw_bucket.get("description")),
                }
            )
        groups.append({"name": raw_group.get("name", "Unknown"), "buckets": buckets})
    return {"ok": True, "source": "structured", "groups": groups}


def _parse_percent(text: str) -> float | None:
    text = text.strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return round(float(text), 1)
    except ValueError:
        return None


def _normalize_text_fallback(response: str) -> dict:
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for line in response.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            parts = line.split("  ")
        if len(parts) < 4:
            continue
        group_name, bucket_name, pct_text, reset_time = parts[0], parts[1], parts[2], parts[3]
        remaining_pct = _parse_percent(pct_text)
        if remaining_pct is None:
            continue
        window = _window_from_text(bucket_name)
        bucket = {
            "window": window,
            "remaining_pct": remaining_pct,
            "reset_time": reset_time.strip() or None,
            "reset_in": None,
        }
        groups.setdefault(group_name.strip(), []).append(bucket)
    return {
        "ok": True,
        "source": "text_fallback",
        "groups": [{"name": name, "buckets": buckets} for name, buckets in groups.items()],
    }


def _parse_envelope(stdout: str) -> dict:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON from agy: {exc}") from exc

    if not isinstance(envelope, dict):
        raise ValueError("unexpected agy response type")

    status = envelope.get("status")
    if status != "SUCCESS":
        error = envelope.get("error") or f"agy returned status {status}"
        raise RuntimeError(error)

    if envelope.get("command", {}).get("data", {}).get("groups"):
        return _normalize_structured(envelope)

    response = envelope.get("response")
    if isinstance(response, str) and response.strip():
        return _normalize_text_fallback(response)

    raise ValueError("agy usage response missing structured data and text response")


def fetch_quota(config: Config, timeout: float = 75.0) -> dict:
    """Run agy usage query and return normalized quota data.

    Raises:
        FileNotFoundError: agy binary not found (maps to exit 127).
        RuntimeError/ValueError: other failures (maps to exit 1).
    """
    agy_path = agy_bin_path(config)

    argv = [
        agy_path,
        "-p",
        "/usage",
        "--output-format",
        "json",
        "--print-timeout",
        "60s",
    ]

    try:
        result = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"agy not found: {agy_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agy quota query timed out after {timeout}s") from exc

    if result.returncode != 0:
        err = result.stderr.strip() or f"agy exited {result.returncode}"
        raise RuntimeError(err)

    return _parse_envelope(result.stdout)
