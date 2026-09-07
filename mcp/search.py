"""
search.py - BM25 full-text search over loaded RuleDocs.
            Headings (H1/H2/H3) and frontmatter title/tags are weighted 2× in
            the index. Snippets are annotated with the parent markdown heading
            they appear under.
"""

import logging
import os
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from loader import RuleDoc, RulesStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TOP_K = 10
_DEFAULT_SNIPPET_WINDOW = 300


def _snippet_window() -> int:
    """Resolve snippet window (chars) from MCP_SNIPPET_SIZE env or default."""
    raw = os.environ.get("MCP_SNIPPET_SIZE", "").strip()
    if not raw:
        return _DEFAULT_SNIPPET_WINDOW
    try:
        v = int(raw)
        if v < 50:
            logger.warning("MCP_SNIPPET_SIZE=%s too small; clamping to 50.", raw)
            return 50
        if v > 5000:
            logger.warning("MCP_SNIPPET_SIZE=%s too large; clamping to 5000.", raw)
            return 5000
        return v
    except ValueError:
        logger.warning("MCP_SNIPPET_SIZE=%r not an integer; using default.", raw)
        return _DEFAULT_SNIPPET_WINDOW


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    project: str
    relative_path: str
    doc_type: str
    score: float
    snippet: str  # Best matching excerpt
    heading: str | None  # Nearest preceding markdown heading (for context)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """
    Lowercase, split on non-alphanumeric, filter stop words.
    Preserves technical terms: camelCase split, kebab-case split.
    """
    # Split camelCase: "errorHandler" → "error handler"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Split kebab/snake: "error-handler" → "error handler"
    text = re.sub(r"[-_]", " ", text)
    tokens = re.findall(r"\b[a-zA-Z0-9]{2,}\b", text.lower())

    stop_words = {
        "the",
        "and",
        "for",
        "are",
        "not",
        "use",
        "you",
        "this",
        "with",
        "from",
        "that",
        "have",
        "will",
        "can",
        "all",
        "was",
        "but",
        "its",
        "also",
        "any",
        "your",
    }
    return [t for t in tokens if t not in stop_words]


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------


def _extract_headings(content: str) -> list[str]:
    """Return all H1/H2/H3 heading texts (without the leading hashes)."""
    headings: list[str] = []
    for line in content.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            headings.append(m.group(2))
    return headings


def _heading_for_line(lines: list[str], idx: int) -> str | None:
    """Walk back from line `idx` to find the nearest preceding H1/H2/H3."""
    for j in range(idx, -1, -1):
        m = _HEADING_RE.match(lines[j])
        if m:
            return m.group(2)
    return None


# ---------------------------------------------------------------------------
# Snippet extractor
# ---------------------------------------------------------------------------


def _extract_snippet(
    content: str,
    query_tokens: list[str],
    window: int,
) -> tuple[str, str | None]:
    """
    Find the best matching region in the doc and return (snippet, parent_heading).
    """
    lines = content.splitlines()
    if not lines:
        return "", None

    best_line_idx = 0
    best_score = 0

    for i, line in enumerate(lines):
        line_tokens = set(_tokenize(line))
        score = sum(1 for t in query_tokens if t in line_tokens)
        if score > best_score:
            best_score = score
            best_line_idx = i

    start = max(0, best_line_idx - 3)
    end = min(len(lines), best_line_idx + 7)
    snippet = "\n".join(lines[start:end]).strip()

    if len(snippet) > window * 2:
        snippet = snippet[: window * 2] + "..."

    heading = _heading_for_line(lines, best_line_idx)
    return snippet, heading


# ---------------------------------------------------------------------------
# Search engine
# ---------------------------------------------------------------------------


class RulesSearchEngine:
    def __init__(self, store: RulesStore) -> None:
        self._docs = store.all_docs()
        self._snippet_window = _snippet_window()
        self._index = self._build_index(self._docs) if self._docs else None
        logger.info("BM25 index built over %d documents.", len(self._docs))

    def rebuild(self, store: RulesStore) -> None:
        """Rebuild the index after a corpus reload (atomic swap upstream)."""
        self._docs = store.all_docs()
        self._index = self._build_index(self._docs) if self._docs else None
        logger.info("BM25 index rebuilt over %d documents.", len(self._docs))

    def _build_index(self, docs: list[RuleDoc]) -> BM25Okapi:
        corpus: list[list[str]] = []
        for doc in docs:
            # Path / type tokens: identify the doc.
            path_tokens = _tokenize(doc.relative_path + " " + doc.project + " " + doc.doc_type)
            content_tokens = _tokenize(doc.content)

            # Boosted tokens: headings + frontmatter title/tags weighted 2×.
            heading_text = " ".join(_extract_headings(doc.content))
            heading_tokens = _tokenize(heading_text)
            meta_title = str(doc.metadata.get("title", ""))
            meta_tags_raw = doc.metadata.get("tags", [])
            if isinstance(meta_tags_raw, list):
                meta_tags = " ".join(str(t) for t in meta_tags_raw)
            else:
                meta_tags = str(meta_tags_raw)
            meta_tokens = _tokenize(meta_title + " " + meta_tags)

            boost = heading_tokens + meta_tokens
            corpus.append(path_tokens + content_tokens + boost + boost)
        return BM25Okapi(corpus)

    def search(
        self,
        query: str,
        project: str | None = None,
        doc_type: str | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[SearchResult]:
        """
        BM25 search over docs.

        Filters (project / doc_type) are applied BEFORE truncating at top_k,
        so a filtered search never under-returns.

        Args:
            query:    Natural language or keyword query.
            project:  Optional - filter results to a single project.
            doc_type: Optional - filter by type (pattern, agents, workflow, ...).
            top_k:    Max results to return.

        Returns:
            Ranked list of SearchResult, best match first.
        """
        if not query.strip() or self._index is None:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = self._index.get_scores(query_tokens)

        # Pre-filter candidate set, then rank - never truncate before filtering.
        candidates: list[tuple[RuleDoc, float]] = []
        for doc, score in zip(self._docs, scores, strict=True):
            if score == 0.0:
                continue
            if project and doc.project != project:
                continue
            if doc_type and doc.doc_type != doc_type:
                continue
            candidates.append((doc, float(score)))

        candidates.sort(key=lambda x: x[1], reverse=True)

        results: list[SearchResult] = []
        for doc, score in candidates[:top_k]:
            snippet, heading = _extract_snippet(doc.content, query_tokens, self._snippet_window)
            results.append(
                SearchResult(
                    project=doc.project,
                    relative_path=doc.relative_path,
                    doc_type=doc.doc_type,
                    score=round(score, 4),
                    snippet=snippet,
                    heading=heading,
                )
            )

        logger.debug("Search '%s' → %d results", query, len(results))
        return results
