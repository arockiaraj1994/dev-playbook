"""Tests for refs.py - the `ref` doc-address grammar.

This grammar is the contract between three things that must never disagree:
frontmatter (`see_also:` / `targets:`), the playbook_get tool, and the CI
validator. It replaces v0.7.0's kind/name/section/depth arguments and the
`_format_call` switch that translated between them.
"""

from __future__ import annotations

import pytest

from refs import (
    REF_KINDS,
    Ref,
    RefError,
    format_ref_call,
    parse_ref,
    relative_path,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("agents", Ref("agents")),
        ("guardrails", Ref("guardrails")),
        ("architecture", Ref("architecture")),
        ("architecture:0001-foo", Ref("architecture", "0001-foo")),
        ("language:kotlin", Ref("language", "kotlin", "standards")),
        ("language:kotlin/testing", Ref("language", "kotlin", "testing")),
        ("language:java/anti-patterns", Ref("language", "java", "anti-patterns")),
        ("pattern:repository", Ref("pattern", "repository")),
        ("skill:add-screen", Ref("skill", "add-screen")),
        ("workflow:bug-fix", Ref("workflow", "bug-fix")),
        ("gate", Ref("gate")),
        ("gate:verify-java", Ref("gate", "verify-java")),
        ("req:ST-101", Ref("req", "ST-101")),
        ("  pattern:repository  ", Ref("pattern", "repository")),
    ],
)
def test_parse_ref(raw: str, expected: Ref) -> None:
    assert parse_ref(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Legacy frontmatter spellings are doc kinds, not tool names, so they
        # keep resolving - a corpus does not have to be rewritten for them.
        ("gates:verify-java", Ref("gate", "verify-java")),
        ("gates:README", Ref("gate")),
        ("requirement:ST-101", Ref("req", "ST-101")),
        ("core:guardrails", Ref("guardrails")),
        ("core:definition-of-done", Ref("guardrails")),
        ("architecture:overview", Ref("architecture")),
    ],
)
def test_parse_ref_legacy_spellings(raw: str, expected: Ref) -> None:
    assert parse_ref(raw) == expected


@pytest.mark.parametrize(
    "bad,message_contains",
    [
        ("", "required"),
        ("   ", "required"),
        ("nonsense", "Unknown ref kind"),
        ("nonsense:x", "Unknown ref kind"),
        ("pattern", "needs a name"),
        ("skill", "needs a name"),
        ("req", "needs a name"),
        ("agents:x", "takes no name"),
        ("guardrails:nope", "Unknown core doc"),
        ("language:kotlin/nope", "Unknown language section"),
    ],
)
def test_parse_ref_rejects(bad: str, message_contains: str) -> None:
    with pytest.raises(RefError) as exc:
        parse_ref(bad)
    assert message_contains in str(exc.value)


@pytest.mark.parametrize(
    "raw", ["agents", "pattern:foo", "language:kotlin/testing", "gate", "req:ST-1"]
)
def test_ref_round_trips_through_its_string(raw: str) -> None:
    """str(Ref) must re-parse to the same Ref, so a rendered Next Call can be
    pasted straight back into playbook_get."""
    once = parse_ref(raw)
    assert parse_ref(str(once)) == once


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("agents", "AGENTS.md"),
        ("architecture", "architecture/overview.md"),
        ("architecture:0007-foo", "architecture/decisions/0007-foo.md"),
        ("language:java", "languages/java/standards.md"),
        ("language:java/testing", "languages/java/testing.md"),
        ("pattern:react", "patterns/react.md"),
        ("skill:deploy", "skills/deploy.md"),
        ("workflow:bug-fix", "workflows/bug-fix.md"),
        ("gate", "gates/README.md"),
    ],
)
def test_relative_path(raw: str, expected: str) -> None:
    assert relative_path(parse_ref(raw)) == expected


@pytest.mark.parametrize("raw", ["guardrails", "req:ST-101"])
def test_relative_path_none_for_looked_up_kinds(raw: str) -> None:
    """guardrails spans two files and req is an id lookup: neither has a single
    static path, and callers must go through render_ref instead."""
    assert relative_path(parse_ref(raw)) is None


def test_every_kind_renders_a_call() -> None:
    """No ref kind may render nothing - a dropped bullet is a dead end for the
    agent, which is the bug b6a3b49 was filed for."""
    examples = {
        "agents": "agents",
        "guardrails": "guardrails",
        "architecture": "architecture:0001-foo",
        "language": "language:kotlin/testing",
        "pattern": "pattern:repository",
        "skill": "skill:add-screen",
        "workflow": "workflow:bug-fix",
        "gate": "gate:verify-java",
        "req": "req:ST-101",
    }
    assert set(examples) == set(REF_KINDS), "a ref kind has no example here"
    for kind, raw in examples.items():
        ref = parse_ref(raw)
        rendered = format_ref_call("nexre", ref)
        assert rendered, kind
        assert f'ref="{ref}"' in rendered, rendered
        assert 'playbook_get(project="nexre"' in rendered, rendered


def test_rendered_call_is_the_frontmatter_string() -> None:
    """The point of the redesign: a see_also entry and the call that follows it
    carry the same string, with no translation step in between."""
    assert (
        format_ref_call("nexre", parse_ref("pattern:repository"))
        == '`playbook_get(project="nexre", ref="pattern:repository")` - pattern `repository`'
    )
