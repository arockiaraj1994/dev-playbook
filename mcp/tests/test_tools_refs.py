"""Tests for the ref grammar (tools/refs.py)."""

from __future__ import annotations

import pytest

from tools.refs import candidate_paths, format_call, format_ref


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("guardrails", "core/guardrails.md"),
        ("definition-of-done", "core/definition-of-done.md"),
        ("dod", "core/definition-of-done.md"),
        ("git", "core/git.md"),
        ("agents", "AGENTS.md"),
        ("architecture", "ARCHITECTURE.md"),
        ("workflow:bug-fix", "workflows/bug-fix.md"),
        ("workflows:release", "workflows/release.md"),
        ("gate:scripts/verify-java.sh", "gates/scripts/verify-java.sh"),
    ],
)
def test_alias_resolves(ref: str, expected: str):
    assert expected in candidate_paths(ref)


def test_exact_path_is_tried_first():
    """A caller echoing back a path we printed must not be second-guessed."""
    assert candidate_paths("core/guardrails.md")[0] == "core/guardrails.md"
    assert candidate_paths("languages/java/testing.md") == ["languages/java/testing.md"]


def test_bare_name_tries_the_places_docs_live():
    paths = candidate_paths("bug-fix")
    assert "workflows/bug-fix.md" in paths
    assert "core/bug-fix.md" in paths


def test_empty_ref_yields_nothing():
    assert candidate_paths("") == []
    assert candidate_paths("   ") == []
    assert candidate_paths(None) == []  # type: ignore[arg-type]


def test_leading_slash_is_tolerated():
    assert "core/guardrails.md" in candidate_paths("/core/guardrails.md")


def test_unknown_prefix_does_not_invent_a_path():
    """v0.8.0's grammar had pattern:/language: kinds; they are not aliases now."""
    assert candidate_paths("pattern:repository") == ["pattern:repository"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("core/guardrails.md", "guardrails"),
        ("AGENTS.md", "agents"),
        ("workflows/bug-fix.md", "workflow:bug-fix"),
        ("gates/scripts/verify-java.sh", "gate:scripts/verify-java.sh"),
        ("languages/java/testing.md", "languages/java/testing.md"),
    ],
)
def test_format_ref_shortens(path: str, expected: str):
    assert format_ref(path) == expected


def test_format_ref_round_trips():
    """Whatever we print in a Next Call has to resolve back to the same row."""
    for path in (
        "core/guardrails.md",
        "core/definition-of-done.md",
        "AGENTS.md",
        "workflows/bug-fix.md",
        "gates/scripts/verify-java.sh",
        "languages/java/testing.md",
    ):
        assert path in candidate_paths(format_ref(path))


def test_format_call_is_a_literal_tool_call():
    assert format_call("nexre", "core/guardrails.md") == (
        'playbook_get_standard(project="nexre", ref="guardrails")'
    )
