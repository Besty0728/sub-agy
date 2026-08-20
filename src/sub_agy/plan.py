"""Plan frontmatter parsing and prompt assembly."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


MAX_PROMPT_BYTES = 200 * 1024


@dataclass
class Plan:
    scope: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    body: str = ""


def parse_plan(text: str) -> Plan:
    """Parse a plan file with optional frontmatter.

    Frontmatter supports:
      key: value
      key: [a, b, c]
      key:
        - a
        - b
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return Plan(body=text.strip())

    end = text.find("\n---\n", 4)
    if end == -1:
        return Plan(body=text.strip())

    front_text = text[4:end]
    body = text[end + 5 :].strip()
    plan = Plan(body=body)

    current_key: str | None = None
    for raw_line in front_text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_key is None:
                continue
            item = stripped[2:].strip()
            getattr(plan, current_key).append(item)
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key not in {"scope", "acceptance", "constraints"}:
                continue
            current_key = key
            if not value:
                continue
            if value.startswith("[") and value.endswith("]"):
                items = [v.strip().strip('"').strip("'") for v in _split_inline_list(value[1:-1])]
                getattr(plan, key).extend(i for i in items if i)
            else:
                getattr(plan, key).append(value)
        else:
            current_key = None

    return plan


def _split_inline_list(value: str) -> list[str]:
    """Split a comma-separated inline list, respecting quotes."""
    parts: list[str] = []
    current: list[str] = []
    in_quotes: str | None = None
    for ch in value:
        if ch in ('"', "'"):
            if in_quotes == ch:
                in_quotes = None
            elif in_quotes is None:
                in_quotes = ch
            current.append(ch)
        elif ch == "," and in_quotes is None:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _fmt_list(items: list[str], fallback: str) -> str:
    if not items:
        return fallback
    return "\n".join(f"  - {item}" for item in items)


def assemble_prompt(
    plan: Plan,
    worktree_or_cwd: str,
    round_number: int = 1,
    prev_summary: str = "",
    feedback_message: str = "",
) -> str:
    """Assemble the prompt sent to agy."""
    scope = _fmt_list(plan.scope, "未声明，自行保守判断")
    acceptance = _fmt_list(plan.acceptance, "未声明")
    constraints = _fmt_list(plan.constraints, "无")

    if round_number >= 2:
        body = (
            f"上一轮反馈：{feedback_message}\n\n"
            f"上一轮 summary：{prev_summary}\n\n"
            "请修复后重新满足上述契约。"
        )
    else:
        body = plan.body

    prompt = f"""{body}

---
## 执行契约（sub-agy 注入，必须遵守）
- 你在一个专用 git worktree 中非交互执行：{worktree_or_cwd}。只在此目录内工作。
- 范围限制：只允许修改 {scope} 内的文件。
- 结束前必须运行并通过验收检查：
{acceptance}
- 约束：
{constraints}
- 结束前用 conventional commit 把你的改动提交到当前分支。
- 最终回答必须满足附带的 JSON schema（structured_output）：summary、files_changed、tests_ran、tests_passed、acceptance_met、blockers、followups。
- 产物一律直接写普通文件并 git 提交；严禁把 worktree 内文件作为 artifact 输出（不要用 artifact/write_to_file 类工具写 worktree 绝对路径，artifact 仅允许在 brain 目录），否则会触发收尾误报。
"""
    return prompt


def write_plan_files(
    job_dir: Path,
    plan: Plan,
    prompt: str,
    schema_text: str,
) -> None:
    """Persist plan.md, prompt.txt, schema.json for a job."""
    body = plan.body
    scope_line = ""
    if plan.scope:
        scope_line = f"scope: {plan.scope}\n"
    acceptance_lines = ""
    if plan.acceptance:
        acceptance_lines = "acceptance:\n" + "".join(f"  - {item}\n" for item in plan.acceptance)
    constraints_lines = ""
    if plan.constraints:
        constraints_lines = "constraints:\n" + "".join(f"  - {item}\n" for item in plan.constraints)

    front = "---\n"
    if scope_line:
        front += scope_line
    if acceptance_lines:
        front += acceptance_lines
    if constraints_lines:
        front += constraints_lines
    front += "---\n"

    plan_path = job_dir / "plan.md"
    plan_path.write_text(front + "\n" + body + "\n", encoding="utf-8")

    prompt_path = job_dir / "prompt.txt"
    prompt_bytes = prompt.encode("utf-8")
    if len(prompt_bytes) > MAX_PROMPT_BYTES:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_BYTES} bytes")
    prompt_path.write_bytes(prompt_bytes)

    schema_path = job_dir / "schema.json"
    schema_path.write_text(schema_text, encoding="utf-8")
