"""Tests for search.py."""

from __future__ import annotations

from pathlib import Path

from loader import load_store
from search import RulesSearchEngine, _extract_headings, _heading_for_line, _tokenize

# -- tokenizer --------------------------------------------------------------


def test_tokenize_lowercases_and_filters_stopwords() -> None:
    tokens = _tokenize("The quick BROWN fox")
    assert "the" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens


def test_tokenize_splits_camel_case() -> None:
    tokens = _tokenize("errorHandler")
    assert "error" in tokens
    assert "handler" in tokens


def test_tokenize_splits_kebab_and_snake() -> None:
    tokens = _tokenize("error-handler dead_letter_queue")
    for t in ("error", "handler", "dead", "letter", "queue"):
        assert t in tokens


def test_tokenize_drops_one_char_tokens() -> None:
    assert _tokenize("a b cd") == ["cd"]


# -- heading helpers --------------------------------------------------------


def test_extract_headings() -> None:
    md = "# Title\n\nintro\n\n## Sub\n\nbody\n\n### Deep\n\nfoo\n#### too deep"
    assert _extract_headings(md) == ["Title", "Sub", "Deep"]


def test_heading_for_line_finds_nearest_above() -> None:
    lines = [
        "# Top",
        "intro",
        "## Section A",
        "body 1",
        "body 2",
        "## Section B",
        "body 3",
    ]
    assert _heading_for_line(lines, 4) == "Section A"
    assert _heading_for_line(lines, 6) == "Section B"
    assert _heading_for_line(lines, 0) == "Top"


# -- search engine ----------------------------------------------------------


def test_search_returns_results(tmp_rules_root: Path) -> None:
    store = load_store(tmp_rules_root)
    engine = RulesSearchEngine(store)
    results = engine.search("DLQ flows")
    assert results, "expected at least one result"
    top = results[0]
    assert top.relative_path == "patterns/foo.md"
    assert top.heading is not None


def test_search_project_filter(tmp_rules_root: Path) -> None:
    store = load_store(tmp_rules_root)
    engine = RulesSearchEngine(store)
    results = engine.search("agents", project="proj-b")
    for r in results:
        assert r.project == "proj-b"


def test_search_doc_type_filter(tmp_rules_root: Path) -> None:
    store = load_store(tmp_rules_root)
    engine = RulesSearchEngine(store)
    results = engine.search("foo bar baz", doc_type="pattern")
    for r in results:
        assert r.doc_type == "pattern"


def test_search_empty_query() -> None:
    from loader import RulesStore

    engine = RulesSearchEngine(RulesStore(docs=[]))
    assert engine.search("") == []
    assert engine.search("   ") == []


def test_search_filter_before_truncate(tmp_rules_root: Path) -> None:
    """Filtered search must not under-return: filter BEFORE top_k truncate.

    Regression guard for the project/doc_type filter path.
    """
    store = load_store(tmp_rules_root)
    engine = RulesSearchEngine(store)
    # Broad query that hits many docs; restrict to pattern and ask for many.
    all_patterns = engine.search("proj", doc_type="pattern", top_k=50)
    assert all_patterns, "expected pattern hits"
    for r in all_patterns:
        assert r.doc_type == "pattern"
    # top_k smaller than the unfiltered corpus must still return only patterns
    limited = engine.search("proj", doc_type="pattern", top_k=1)
    assert len(limited) == 1
    assert limited[0].doc_type == "pattern"


def test_search_results_carry_project_and_type(tmp_rules_root: Path) -> None:
    store = load_store(tmp_rules_root)
    engine = RulesSearchEngine(store)
    results = engine.search("guardrails", top_k=5)
    assert results
    for r in results:
        assert r.project and r.doc_type


def test_search_includes_heading_in_result(tmp_rules_root: Path) -> None:
    store = load_store(tmp_rules_root)
    engine = RulesSearchEngine(store)
    results = engine.search("DLQ flows", top_k=5)
    assert results
    assert any(r.heading for r in results)
