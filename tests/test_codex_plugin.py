"""Tests for the Codex skill plugin packaging."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugins-codex" / "subagy"
MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
SKILLS_DIR = PLUGIN_DIR / "skills"
CLI_PATH = ROOT / "src" / "sub_agy" / "cli.py"

SUPPORTED_SUBCOMMANDS = {
    "run",
    "status",
    "result",
    "watch",
    "feedback",
    "cancel",
    "list",
    "cleanup",
    "doctor",
    "quota",
    "pending",  # §18.2
}

SKILL_FILES = [
    SKILLS_DIR / "subagy-dispatch" / "SKILL.md",
    SKILLS_DIR / "subagy-harvest" / "SKILL.md",
    SKILLS_DIR / "subagy-runtime" / "SKILL.md",
]


@pytest.fixture
def plugin_manifest():
    path = PLUGIN_DIR / ".codex-plugin" / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def marketplace_manifest():
    return json.loads(MARKETPLACE.read_text(encoding="utf-8"))


def test_plugin_manifest_schema(plugin_manifest):
    assert plugin_manifest["name"] == "subagy"
    assert plugin_manifest["version"] == "0.1.1"
    assert "description" in plugin_manifest and plugin_manifest["description"]
    assert plugin_manifest.get("author", {}).get("name") == "betsy"
    assert plugin_manifest.get("license") == "MIT"
    assert plugin_manifest["skills"] == "./skills/"

    interface = plugin_manifest.get("interface", {})
    assert interface.get("displayName") == "sub-agy"
    assert interface.get("category") == "Developer Tools"
    assert "Read" in interface.get("capabilities", [])
    assert "Write" in interface.get("capabilities", [])
    assert len(interface.get("defaultPrompt", [])) >= 3


def test_marketplace_manifest_schema(marketplace_manifest):
    assert "name" in marketplace_manifest
    assert "interface" in marketplace_manifest
    assert "displayName" in marketplace_manifest["interface"]
    plugins = marketplace_manifest.get("plugins", [])
    assert len(plugins) == 1
    entry = plugins[0]
    assert entry["name"] == "subagy"
    assert entry["source"]["source"] == "local"
    assert entry["source"]["path"] == "./plugins-codex/subagy"
    assert entry["policy"]["installation"] == "AVAILABLE"
    assert entry["policy"]["authentication"] == "ON_INSTALL"
    assert entry["category"] == "Developer Tools"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm_text = parts[1].strip()
    body = parts[2].strip()
    fm: dict[str, str] = {}
    key: str | None = None
    for line in fm_text.splitlines():
        if ":" in line and not line.strip().startswith("-"):
            key, value = line.split(":", 1)
            fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm, body


@pytest.mark.parametrize("skill_path", SKILL_FILES)
def test_skill_frontmatter(skill_path):
    text = skill_path.read_text(encoding="utf-8")
    fm, _ = _parse_frontmatter(text)
    assert "name" in fm and fm["name"], f"{skill_path} missing name frontmatter"
    assert "description" in fm and fm["description"], f"{skill_path} missing description frontmatter"


@pytest.mark.parametrize("skill_path", SKILL_FILES)
def test_skill_no_claude_plugin_root(skill_path):
    text = skill_path.read_text(encoding="utf-8")
    assert "CLAUDE_PLUGIN_ROOT" not in text, f"{skill_path} must not reference CLAUDE_PLUGIN_ROOT"


def _extract_command_subcommands(text: str) -> set[str]:
    """Extract subcommand candidates that follow a literal `sub-agy` invocation.

    Matches:
    - backtick-wrapped: `sub-agy run ...`
    - code-block lines starting with sub-agy
    Ignores prose like "sub-agy dispatch skill" or headings.
    """
    subcommands: set[str] = set()

    # Backtick-wrapped commands anywhere in the text.
    for match in re.finditer(r"`([^`\n]+)`", text):
        cmd = match.group(1).strip()
        if cmd.startswith("sub-agy "):
            parts = cmd.split()
            if len(parts) >= 2:
                subcommands.add(parts[1])

    # Lines inside code blocks (```bash or plain ```).
    in_code_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block and stripped.startswith("sub-agy "):
            parts = stripped.split()
            if len(parts) >= 2:
                subcommands.add(parts[1])

    return subcommands


@pytest.mark.parametrize("skill_path", SKILL_FILES)
def test_skill_mentions_only_supported_subcommands(skill_path):
    text = skill_path.read_text(encoding="utf-8")
    matches = _extract_command_subcommands(text)
    unsupported = matches - SUPPORTED_SUBCOMMANDS
    assert not unsupported, f"{skill_path} mentions unsupported subcommands: {unsupported}"


def test_cli_declares_expected_subcommands():
    source = CLI_PATH.read_text(encoding="utf-8")
    # Locate the _SUBCOMMANDS set to verify it matches our expected supported set.
    match = re.search(r"_SUBCOMMANDS\s*=\s*\{([^}]*)\}", source, re.DOTALL)
    assert match, "_SUBCOMMANDS set not found in cli.py"
    raw = match.group(1)
    declared = {m.strip('"\'') for m in re.findall(r'["\']([^"\']+)["\']', raw)}
    # _supervise is internal and not user-facing; exclude from the comparison.
    declared_user = declared - {"_supervise"}
    assert declared_user == SUPPORTED_SUBCOMMANDS, f"declared {declared_user} != expected {SUPPORTED_SUBCOMMANDS}"
