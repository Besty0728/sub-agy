"""Tests for plan frontmatter parsing and prompt assembly."""

from __future__ import annotations

from sub_agy.plan import Plan, assemble_prompt, parse_plan


def test_parse_frontmatter_scalar_and_list() -> None:
    text = """---
scope: [src/auth/**]
acceptance:
  - pytest tests/auth -q 通过
  - 不修改 scope 外文件
constraints:
  - 不新增依赖
---
# 正文
Fix auth bug.
"""
    plan = parse_plan(text)
    assert plan.scope == ["src/auth/**"]
    assert plan.acceptance == ["pytest tests/auth -q 通过", "不修改 scope 外文件"]
    assert plan.constraints == ["不新增依赖"]
    assert plan.body == "# 正文\nFix auth bug."


def test_parse_inline_list() -> None:
    text = """---
scope: [a, b, c]
---
body
"""
    plan = parse_plan(text)
    assert plan.scope == ["a", "b", "c"]
    assert plan.body == "body"


def test_parse_no_frontmatter() -> None:
    plan = parse_plan("just a body")
    assert plan.body == "just a body"
    assert plan.scope == []


def test_parse_malformed_frontmatter_falls_back() -> None:
    text = """---
this is not key value
---
body
"""
    plan = parse_plan(text)
    # Unknown keys are ignored; body is parsed normally.
    assert plan.body == "body"
    assert plan.scope == []


def test_assemble_prompt_includes_contract() -> None:
    plan = Plan(
        scope=["src/**"],
        acceptance=["tests pass"],
        constraints=["no deps"],
        body="Do work.",
    )
    prompt = assemble_prompt(plan, "/worktree")
    assert "Do work." in prompt
    assert "/worktree" in prompt
    assert "src/**" in prompt
    assert "tests pass" in prompt
    assert "no deps" in prompt
    assert "conventional commit" in prompt


def test_assemble_prompt_round_two() -> None:
    plan = Plan(body="Do work.")
    prompt = assemble_prompt(
        plan,
        "/worktree",
        round_number=2,
        prev_summary="previous",
        feedback_message="fix it",
    )
    assert "上一轮反馈：fix it" in prompt
    assert "上一轮 summary：previous" in prompt
    assert "conventional commit" in prompt
