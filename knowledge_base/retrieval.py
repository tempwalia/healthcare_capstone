"""BM25 lexical retrieval over the local policy knowledge base (`nb/`).

Deliberately not embedding-based: this app's only configured LLM (Groq, via
`app.agents.llm.get_chat_model`) has no embeddings endpoint, and every other
AI feature here runs with zero API keys required (ADR-005) — BM25 keeps that
property for the knowledge base too, rather than introducing a second,
optional credential just for retrieval. See `nb/README.md`.

The index is built lazily on first use and rebuilt automatically whenever a
file under `nb/` is added, removed, or its mtime changes, so editing a
document takes effect without a process restart — cheap to check given the
corpus here is a handful of short documents, not worth a background watcher.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from rank_bm25 import BM25Plus

from app.agents.nodes.intake import extract_document_text

KB_ROOT = Path(__file__).resolve().parents[1] / "nb"

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_EXCERPT_LENGTH = 500


def _tokenize(text: str) -> List[str]:
    return _TOKEN_PATTERN.findall(text.lower())


@dataclass
class KBDocument:
    doc_id: str  # path relative to KB_ROOT, e.g. "policies/referral_process_guide.txt"
    title: str
    category: str
    text: str
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def excerpt(self) -> str:
        body = self.text.strip()
        return body[:_EXCERPT_LENGTH] + ("..." if len(body) > _EXCERPT_LENGTH else "")


def _parse_header(text: str) -> Dict[str, str]:
    """Every document here opens with a small `KEY: value` header block
    (TITLE/CATEGORY/etc, see nb/README.md) ending at the first blank line —
    parsed generically so new header fields don't need code changes."""
    header: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        header[key.strip().upper()] = value.strip()
    return header


def _load_document(path: Path) -> Optional[KBDocument]:
    text = extract_document_text(str(path))
    if not text.strip():
        return None
    header = _parse_header(text)
    doc_id = str(path.relative_to(KB_ROOT)).replace("\\", "/")
    return KBDocument(
        doc_id=doc_id,
        title=header.get("TITLE", path.stem.replace("_", " ").title()),
        category=header.get("CATEGORY", path.parent.name.title()),
        text=text,
        metadata=header,
    )


class _KBIndex:
    """Holds the loaded documents + BM25 index, invalidated by file mtimes."""

    def __init__(self) -> None:
        self._documents: Dict[str, KBDocument] = {}
        self._bm25: Optional[BM25Plus] = None
        self._doc_order: List[str] = []
        self._mtimes: Dict[str, float] = {}

    def _current_files(self) -> Dict[Path, float]:
        if not KB_ROOT.exists():
            return {}
        return {
            path: path.stat().st_mtime
            for path in sorted(KB_ROOT.rglob("*"))
            if path.is_file() and path.suffix.lower() in (".txt", ".pdf") and path.name != "README.md"
        }

    def _stale(self) -> bool:
        current = {str(p): m for p, m in self._current_files().items()}
        return current != self._mtimes

    def ensure_fresh(self) -> None:
        if self._bm25 is not None and not self._stale():
            return
        files = self._current_files()
        documents: Dict[str, KBDocument] = {}
        for path in files:
            doc = _load_document(path)
            if doc is not None:
                documents[doc.doc_id] = doc

        self._documents = documents
        self._doc_order = sorted(documents.keys())
        corpus = [_tokenize(documents[doc_id].text) for doc_id in self._doc_order]
        # BM25Plus, not the more common BM25Okapi: classic Robertson/Sparck-Jones
        # IDF (what Okapi uses) goes to exactly zero for any term appearing in
        # precisely half the corpus, and negative above that — a real problem
        # here, not a theoretical one, since the "how to compare this plan"
        # section of every insurance/ document deliberately names the *other*
        # plans too, and this corpus is small (8 documents), so query terms
        # like "acme"/"copay" land on exactly 4-of-8 easily. BM25Plus's lower-bound
        # term keeps every matching document's score positive and meaningfully
        # ranked even when raw IDF would collapse to ~0 — confirmed against this
        # exact corpus, see the "compare Acme PPO Gold and copay" case in
        # tests/test_knowledge_base.py.
        self._bm25 = BM25Plus(corpus) if corpus else None
        self._mtimes = {str(p): m for p, m in files.items()}

    def documents(self) -> Dict[str, KBDocument]:
        self.ensure_fresh()
        return self._documents

    def search(self, query: str, top_k: int) -> List[dict]:
        self.ensure_fresh()
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self._doc_order, scores), key=lambda pair: pair[1], reverse=True)
        results = []
        for doc_id, score in ranked[:top_k]:
            if score <= 0:
                continue
            doc = self._documents[doc_id]
            results.append({
                "doc_id": doc.doc_id,
                "title": doc.title,
                "category": doc.category,
                "score": round(float(score), 4),
                "excerpt": doc.excerpt,
            })
        return results


_index = _KBIndex()


def search(query: str, top_k: int = 5) -> List[dict]:
    """Ranked matches for `query`. Empty list for a blank query or an empty/
    missing knowledge base — never raises, matching this codebase's
    never-a-500 resilience posture for AI-adjacent features."""
    if not query or not query.strip():
        return []
    return _index.search(query, top_k)


def list_documents() -> List[dict]:
    """Catalog for the `kb://policies` MCP resource — every document's id/
    title/category, no full text (keep the listing itself small)."""
    return [
        {"doc_id": doc.doc_id, "title": doc.title, "category": doc.category}
        for doc in _index.documents().values()
    ]


def get_document(doc_id: str) -> Optional[dict]:
    """Full document for the `kb://policies/{doc_id}` MCP resource template."""
    doc = _index.documents().get(doc_id)
    if doc is None:
        return None
    return {"doc_id": doc.doc_id, "title": doc.title, "category": doc.category, "text": doc.text}
