"""trading_brain_seed vault: valid frontmatter, linked graph, no code."""

import re
from pathlib import Path

import yaml

VAULT = Path(__file__).parent.parent / "trading_brain_seed"
REQUIRED_NOTES = [
    "README.md", "00 Home.md",
    "Templates/Daily Bias.md", "Templates/Trade Review.md", "Templates/Mistake Log.md",
    "Templates/Signal Note.md", "Templates/Weekly Review.md",
    "Strategy Rules/Copilot Trading Rules.md", "Strategy Rules/Risk Rules.md",
    "Setup Playbooks/MTF Session Liquidity Trap.md",
    "Setup Playbooks/ORB Breakout (skeleton).md",
    "Mistakes/_Mistake Index.md",
]


def _notes():
    return sorted(VAULT.rglob("*.md"))


def test_required_notes_exist():
    for rel in REQUIRED_NOTES:
        assert (VAULT / rel).exists(), f"missing vault note: {rel}"


def test_every_note_has_valid_yaml_frontmatter():
    for p in _notes():
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{p.name}: missing frontmatter"
        end = text.index("\n---", 4)
        fm = yaml.safe_load(text[4:end])
        assert isinstance(fm, dict) and "type" in fm and "tags" in fm, f"{p.name}: bad frontmatter"


def test_vault_is_a_graph_not_a_pile():
    """Non-index notes must link somewhere; hub notes must be linked to."""
    all_text = " ".join(p.read_text(encoding="utf-8") for p in _notes())
    for hub in ("[[Copilot Trading Rules]]", "[[_Mistake Index]]",
                "[[MTF Session Liquidity Trap]]", "[[Trade Review]]", "[[Weekly Review]]"):
        assert all_text.count(hub) >= 2, f"hub {hub} under-linked"
    for p in _notes():
        assert re.search(r"\[\[.+?\]\]", p.read_text(encoding="utf-8")), f"{p.name}: no wiki-links"


def test_no_code_in_vault():
    """Rule: the vault holds thinking, not code. CLI one-liners in prose are
    fine; fenced code blocks are not."""
    for p in _notes():
        assert "```" not in p.read_text(encoding="utf-8"), f"{p.name}: fenced code block found"
