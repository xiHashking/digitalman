"""Local, evidence-oriented knowledge-base retrieval for voice Q&A."""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from utils.logger import logger


_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
_DEFAULT_MAX_CHARS = 1200
_DEFAULT_CONTEXT_CHARS = 6000

# Spoken filler should not dominate retrieval. This stays deliberately small;
# domain-specific terms continue to be weighted by document frequency.
_QUERY_STOP_TERMS = {
    "一下", "什么", "怎么", "如何", "是否", "有没有", "可以", "请问",
    "这个", "那个", "现在", "你们", "我们", "帮我", "告诉", "说说",
    "介绍", "了解", "情况", "问题", "觉得", "的是", "的吗", "什么是",
}
_CASUAL_QUERY_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hello|早上好|上午好|下午好|晚上好|谢谢|感谢|"
    r"再见|拜拜|好奇怪|没事|好的|好吧)[啊呀呢吗吧哦哟！!。,.，？?~～]*$",
    re.IGNORECASE,
)
_KB_META_QUERY_RE = re.compile(
    r"(知识库|资料库).{0,8}(有问题|不对|错误|坏了|奇怪|不准确|不靠谱|肯定有问题)"
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _terms(text: str, *, query: bool = False) -> Counter[str]:
    """Return Chinese 2/3-grams and complete latin/number terms."""
    terms: Counter[str] = Counter()
    lowered = (text or "").lower()

    for sequence in _CJK_SEQUENCE_RE.findall(lowered):
        for size in (2, 3):
            for index in range(0, len(sequence) - size + 1):
                term = sequence[index:index + size]
                if query and term in _QUERY_STOP_TERMS:
                    continue
                terms[term] += 1

    for term in _ASCII_TERM_RE.findall(lowered):
        if len(term) >= 2 and (not query or term not in _QUERY_STOP_TERMS):
            terms[term] += 1
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
class KnowledgeMatch:
    title: str
    text: str
    score: float
    coverage: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeSearchResult:
    query: str
    matches: tuple[KnowledgeMatch, ...]
    skipped_reason: str = ""

    def context(self, max_chars: int = _DEFAULT_CONTEXT_CHARS) -> str:
        blocks: list[str] = []
        used = 0
        for index, match in enumerate(self.matches, start=1):
            block = (
                f"[内部资料 {index}]\n"
                f"来源：{match.title}\n"
                f"原文：\n{match.text}"
            )
            if used + len(block) > max_chars:
                break
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)


@dataclass(frozen=True)
class _Chunk:
    scope: str
    title: str
    text: str
    term_counts: Counter[str]
    title_terms: frozenset[str]


class KnowledgeBase:
    def __init__(self, root: Path):
        self.root = root
        self.chunks: list[_Chunk] = []
        self.document_frequency: dict[str, int] = {}
        self.scope_terms: dict[str, frozenset[str]] = {}
        self.scope_frequency: dict[str, int] = {}
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
            scope = title.split("/", 1)[0]
            title_terms = frozenset(_terms(title))
            self.scope_terms.setdefault(scope, frozenset(_terms(scope)))
            for chunk_text in _split_chunks(text):
                term_counts = _terms(chunk_text)
                searchable_terms = set(term_counts) | set(title_terms)
                self.chunks.append(
                    _Chunk(scope, title, chunk_text, term_counts, title_terms)
                )
                for term in searchable_terms:
                    self.document_frequency[term] = (
                        self.document_frequency.get(term, 0) + 1
                    )

        for terms in self.scope_terms.values():
            for term in terms:
                self.scope_frequency[term] = self.scope_frequency.get(term, 0) + 1

        logger.info(
            "[KB] Loaded %d files into %d searchable chunks from %s",
            len(files), len(self.chunks), self.root,
        )

    @staticmethod
    def _skip_reason(query: str) -> str:
        normalized = re.sub(r"\s+", "", (query or "").strip())
        if not normalized:
            return "empty_query"
        if _CASUAL_QUERY_RE.fullmatch(normalized):
            return "casual_conversation"
        if _KB_META_QUERY_RE.search(normalized):
            return "knowledge_base_feedback"
        return ""

    def _select_scopes(self, query_terms: set[str]) -> set[str]:
        """Infer explicit product/topic scopes from discriminative title terms."""
        scope_count = len(self.scope_terms)
        if scope_count <= 1:
            return set(self.scope_terms)

        max_scope_frequency = max(1, math.ceil(scope_count * 0.4))
        scores: dict[str, float] = {}
        for scope, terms in self.scope_terms.items():
            overlap = query_terms & set(terms)
            discriminative = {
                term for term in overlap
                if self.scope_frequency.get(term, 0) <= max_scope_frequency
            }
            if not discriminative:
                continue
            scores[scope] = sum(
                (len(term) - 1)
                * (math.log((scope_count + 1) / (self.scope_frequency[term] + 0.5)) + 1.0)
                for term in discriminative
            )

        if not scores:
            return set()
        best_score = max(scores.values())
        if best_score < 2.0:
            return set()
        return {
            scope for scope, score in scores.items()
            if score >= best_score * 0.7
        }

    def search(self, query: str, top_k: int = 4) -> KnowledgeSearchResult:
        skip_reason = self._skip_reason(query)
        if skip_reason:
            return KnowledgeSearchResult(query=query, matches=(), skipped_reason=skip_reason)

        query_counts = _terms(query, query=True)
        if not query_counts or not self.chunks:
            return KnowledgeSearchResult(
                query=query,
                matches=(),
                skipped_reason=(
                    "no_meaningful_terms" if not query_counts else "empty_knowledge_base"
                ),
            )

        try:
            min_score = max(0.0, float(os.getenv("KB_MIN_SCORE", "6.0")))
        except ValueError:
            min_score = 6.0
        try:
            min_coverage = min(
                1.0,
                max(0.0, float(os.getenv("KB_MIN_COVERAGE", "0.16"))),
            )
        except ValueError:
            min_coverage = 0.16

        total_chunks = len(self.chunks)

        def idf(term: str) -> float:
            frequency = self.document_frequency.get(term, 0)
            return math.log((total_chunks + 1) / (frequency + 0.5)) + 1.0

        query_weight = sum(idf(term) for term in query_counts)
        normalized_query = _normalize_text(query)
        selected_scopes = self._select_scopes(set(query_counts))
        scored: list[KnowledgeMatch] = []

        for chunk in self.chunks:
            if selected_scopes and chunk.scope not in selected_scopes:
                continue
            searchable_terms = set(chunk.term_counts) | set(chunk.title_terms)
            overlap = set(query_counts) & searchable_terms
            if not overlap:
                continue

            overlap_weight = sum(idf(term) for term in overlap)
            title_weight = sum(idf(term) for term in overlap & chunk.title_terms)
            coverage = overlap_weight / query_weight if query_weight else 0.0
            exact_phrase = (
                len(normalized_query) >= 3
                and normalized_query in _normalize_text(chunk.text)
            )
            exact_title = (
                len(normalized_query) >= 3
                and normalized_query in _normalize_text(chunk.title)
            )

            # One short generic overlap is insufficient unless the complete
            # query occurs in the source. This is the main false-positive guard.
            strong_overlap = [term for term in overlap if len(term) >= 3]
            if (
                len(overlap) < 2
                and not strong_overlap
                and not exact_phrase
                and not exact_title
            ):
                continue

            term_frequency_bonus = sum(
                min(chunk.term_counts.get(term, 0), 3) * 0.15
                for term in overlap
            )
            score = (
                overlap_weight
                + (2.0 * title_weight)
                + term_frequency_bonus
                + (8.0 if exact_phrase else 0.0)
                + (10.0 if exact_title else 0.0)
            )
            if score < min_score or coverage < min_coverage:
                continue

            scored.append(
                KnowledgeMatch(
                    title=chunk.title,
                    text=chunk.text,
                    score=score,
                    coverage=coverage,
                    matched_terms=tuple(
                        sorted(overlap, key=lambda term: (-len(term), term))[:12]
                    ),
                )
            )

        scored.sort(key=lambda item: (-item.score, -item.coverage, item.title))

        # Without an explicit product/topic scope, similarly ranked results from
        # different directories are ambiguous. Passing neither is safer than
        # letting the model combine facts from unrelated products.
        if not selected_scopes and scored:
            best_by_scope: dict[str, KnowledgeMatch] = {}
            for match in scored:
                scope = match.title.split("/", 1)[0]
                best_by_scope.setdefault(scope, match)
            scope_leaders = sorted(
                best_by_scope.values(),
                key=lambda item: (-item.score, -item.coverage, item.title),
            )
            if (
                len(scope_leaders) > 1
                and scope_leaders[1].score >= scope_leaders[0].score * 0.8
            ):
                return KnowledgeSearchResult(
                    query=query,
                    matches=(),
                    skipped_reason="ambiguous_scope",
                )

        return KnowledgeSearchResult(query=query, matches=tuple(scored[:top_k]))

    def context(self, query: str) -> str:
        try:
            top_k = max(1, int(os.getenv("KB_TOP_K", "4")))
        except ValueError:
            top_k = 4
        try:
            max_chars = max(
                1000,
                int(os.getenv("KB_MAX_CONTEXT_CHARS", str(_DEFAULT_CONTEXT_CHARS))),
            )
        except ValueError:
            max_chars = _DEFAULT_CONTEXT_CHARS
        return self.search(query, top_k=top_k).context(max_chars=max_chars)


_kb: KnowledgeBase | None = None
_kb_lock = threading.Lock()


def search_knowledge(query: str) -> KnowledgeSearchResult:
    """Search the configured local knowledge base and log auditable hits."""
    global _kb
    start = time.perf_counter()
    root_value = os.getenv("KNOWLEDGE_BASE_DIR", "").strip()
    if not root_value:
        result = KnowledgeSearchResult(
            query=query,
            matches=(),
            skipped_reason="not_configured",
        )
        logger.info("[KB] skipped reason=%s query=%r", result.skipped_reason, query)
        return result

    with _kb_lock:
        if _kb is None or str(_kb.root) != root_value:
            _kb = KnowledgeBase(Path(root_value).expanduser())
        knowledge_base = _kb

    try:
        top_k = max(1, int(os.getenv("KB_TOP_K", "4")))
    except ValueError:
        top_k = 4
    result = knowledge_base.search(query, top_k=top_k)
    elapsed_ms = (time.perf_counter() - start) * 1000
    if result.matches:
        hit_summary = "; ".join(
            f"{match.title} score={match.score:.2f} coverage={match.coverage:.0%} "
            f"terms={','.join(match.matched_terms)}"
            for match in result.matches
        )
        logger.info(
            "[KB] query=%r hits=%d elapsed_ms=%.1f | %s",
            query, len(result.matches), elapsed_ms, hit_summary,
        )
    else:
        logger.info(
            "[KB] query=%r no_hit reason=%s elapsed_ms=%.1f",
            query, result.skipped_reason or "below_threshold", elapsed_ms,
        )
    return result


def get_knowledge_context(query: str) -> str:
    """Compatibility wrapper returning only prompt-ready evidence text."""
    try:
        max_chars = max(
            1000,
            int(os.getenv("KB_MAX_CONTEXT_CHARS", str(_DEFAULT_CONTEXT_CHARS))),
        )
    except ValueError:
        max_chars = _DEFAULT_CONTEXT_CHARS
    return search_knowledge(query).context(max_chars=max_chars)
