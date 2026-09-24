"""Small local knowledge-base retriever for the voice/text assistant."""

from __future__ import annotations

import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from utils.logger import logger


_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")
_DEFAULT_MAX_CHARS = 1200
_DEFAULT_CONTEXT_CHARS = 6000


def _tokens(text: str) -> set[str]:
    """Return CJK unigrams/bigrams plus latin/number terms."""
    terms: set[str] = set()
    for match in _TOKEN_RE.finditer(text.lower()):
        value = match.group(0)
        terms.add(value)
        if len(value) > 1 and not value.isascii():
            terms.update(value[i:i + 2] for i in range(len(value) - 1))
    return terms


def _split_chunks(text: str, max_chars: int = _DEFAULT_MAX_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                paragraph[i:i + max_chars]
                for i in range(0, len(paragraph), max_chars)
            )
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


@dataclass(frozen=True)
class _Chunk:
    title: str
    text: str
    terms: frozenset[str]


class KnowledgeBase:
    def __init__(self, root: Path):
        self.root = root
        self.chunks: list[_Chunk] = []
        self.document_frequency: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.root.is_dir():
            logger.warning("[KB] Directory not found: %s", self.root)
            return

        files = sorted(
            path for path in self.root.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError as exc:
                logger.warning("[KB] Cannot read %s: %s", path, exc)
                continue
            if not text:
                continue
            title = str(path.relative_to(self.root).with_suffix(""))
            for chunk_text in _split_chunks(text):
                terms = frozenset(_tokens(chunk_text) | _tokens(title))
                self.chunks.append(_Chunk(title, chunk_text, terms))
                for term in terms:
                    self.document_frequency[term] = self.document_frequency.get(term, 0) + 1

        logger.info(
            "[KB] Loaded %d files into %d searchable chunks from %s",
            len(files), len(self.chunks), self.root,
        )

    def search(self, query: str, top_k: int = 4) -> list[_Chunk]:
        query_terms = _tokens(query)
        if not query_terms or not self.chunks:
            return []

        total = len(self.chunks)
        scored: list[tuple[float, _Chunk]] = []
        for chunk in self.chunks:
            overlap = query_terms & chunk.terms
            if not overlap:
                continue
            score = sum(
                math.log((total + 1) / (self.document_frequency.get(term, 0) + 1))
                for term in overlap
            )
            if query.lower() in chunk.text.lower():
                score += 2.0
            title_terms = _tokens(chunk.title)
            score += 2.0 * len(query_terms & title_terms)
            scored.append((score / math.sqrt(len(query_terms)), chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for score, chunk in scored[:top_k] if score > 0]

    def context(self, query: str) -> str:
        try:
            top_k = max(1, int(os.getenv("KB_TOP_K", "4")))
        except ValueError:
            top_k = 4
        try:
            max_chars = max(1000, int(os.getenv("KB_MAX_CONTEXT_CHARS", str(_DEFAULT_CONTEXT_CHARS))))
        except ValueError:
            max_chars = _DEFAULT_CONTEXT_CHARS

        blocks: list[str] = []
        used = 0
        seen_titles: set[str] = set()
        for chunk in self.search(query, top_k=top_k):
            if chunk.title in seen_titles:
                continue
            block = f"### {chunk.title}\n{chunk.text}"
            if used + len(block) > max_chars:
                break
            blocks.append(block)
            used += len(block)
            seen_titles.add(chunk.title)
        return "\n\n".join(blocks)


_kb: KnowledgeBase | None = None
_kb_lock = threading.Lock()


def get_knowledge_context(query: str) -> str:
    """Load the configured local KB once and return relevant context."""
    global _kb
    root_value = os.getenv("KNOWLEDGE_BASE_DIR", "").strip()
    if not root_value:
        return ""
    with _kb_lock:
        if _kb is None or str(_kb.root) != root_value:
            _kb = KnowledgeBase(Path(root_value).expanduser())
    return _kb.context(query)
