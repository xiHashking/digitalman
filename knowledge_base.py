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
_PRIORITY_QA_SCOPES = {
    "AI素养评价产品QA",
    "教师发展评价系统常见场景QA",
    "AI宣传招生就业系统常见QA",
}
_QA_QUESTION_RE = re.compile(
    r"^\s*(?:#{2,4}\s*)?(?:【[^】]+】\s*)?Q(?:\d+)?[：:]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_QA_ANSWER_RE = re.compile(
    r"^\s*(?:\*\*)?A[：:](?:\*\*)?\s*(.*)$",
    re.IGNORECASE,
)

# Spoken filler should not dominate retrieval. This stays deliberately small;
# domain-specific terms continue to be weighted by document frequency.
_QUERY_STOP_TERMS = {
    "一下", "什么", "怎么", "如何", "是否", "有没有", "可以", "请问",
    "这个", "那个", "现在", "你们", "我们", "帮我", "告诉", "说说",
    "介绍", "了解", "情况", "问题", "觉得", "的是", "的吗", "什么是",
    "能不能", "能不", "不能", "是不是", "是不", "不是", "会不会",
    "会不", "不会", "要不要",
}
_CASUAL_QUERY_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hello|早上好|上午好|下午好|晚上好|谢谢|感谢|"
    r"再见|拜拜|好奇怪|没事|好的|好吧)[啊呀呢吗吧哦哟！!。,.，？?~～]*$",
    re.IGNORECASE,
)
_KB_META_QUERY_RE = re.compile(
    r"(知识库|资料库).{0,8}(有问题|不对|错误|坏了|奇怪|不准确|不靠谱|肯定有问题)"
)
_QA_ROLE_TERMS = {
    "家长", "考生", "学生", "老师", "教师", "校长", "管理者",
    "教研", "教务", "招生", "就业", "班主任", "信息化",
}
_QA_QUERY_EXPANSIONS = (
    (re.compile(r"孩子|小孩"), "学生"),
    (re.compile(r"闲置|吃灰|没什么用"), "摆设 招生季"),
    (re.compile(r"乱说|说错|答错"), "回答不准确"),
    (re.compile(r"求职材料"), "简历"),
    (re.compile(r"小范围.{0,4}(试|用)"), "试点"),
    (re.compile(r"忙不过来|接待不过来"), "人流量大 现场人很多"),
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _qa_query_terms(query: str) -> Counter[str]:
    counts = _terms(query, query=True)
    for pattern, expansion in _QA_QUERY_EXPANSIONS:
        if pattern.search(query or ""):
            counts.update(_terms(expansion, query=True))
    return counts


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
    kind: str = "document"
    standard_question: str = ""
    standard_answer: str = ""
    semantic_score: float = 0.0


@dataclass(frozen=True)
class KnowledgeSearchResult:
    query: str
    matches: tuple[KnowledgeMatch, ...]
    skipped_reason: str = ""

    def context(self, max_chars: int = _DEFAULT_CONTEXT_CHARS) -> str:
        blocks: list[str] = []
        used = 0
        for index, match in enumerate(self.matches, start=1):
            if match.kind == "qa":
                block = (
                    f"[标准问答 {index}]\n"
                    f"来源：{match.title}\n"
                    f"标准问题：{match.standard_question}\n"
                    f"标准答案：{match.standard_answer}"
                )
            else:
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


@dataclass(frozen=True)
class _QaEntry:
    scope: str
    title: str
    question: str
    answer: str
    question_terms: frozenset[str]
    answer_terms: frozenset[str]
    source_terms: frozenset[str]


class _LocalSemanticQaIndex:
    """Small in-memory semantic index for paraphrased QA questions."""

    def __init__(self, entries: list[_QaEntry]):
        self.entries = entries
        self.model_name = os.getenv(
            "KB_QA_EMBEDDING_MODEL",
            "BAAI/bge-small-zh-v1.5",
        ).strip()
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self._entry_embeddings = None
        self._disabled = False

    @staticmethod
    def enabled() -> bool:
        value = os.getenv("KB_QA_SEMANTIC_ENABLED", "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def warm(self) -> bool:
        if not self.enabled() or not self.entries:
            return False
        with self._lock:
            return self._ensure_loaded()

    def scores(self, query: str) -> list[float] | None:
        if not self.enabled() or not self.entries:
            return None
        with self._lock:
            if not self._ensure_loaded():
                return None
            query_embedding = self._encode([query])
            scores = self._entry_embeddings @ query_embedding[0]
            return [float(score) for score in scores]

    def _ensure_loaded(self) -> bool:
        if self._entry_embeddings is not None:
            return True
        if self._disabled:
            return False

        started = time.perf_counter()
        try:
            from transformers import AutoModel, AutoTokenizer

            local_only = os.getenv(
                "KB_QA_EMBEDDING_LOCAL_ONLY", "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                local_files_only=local_only,
            )
            self._model = AutoModel.from_pretrained(
                self.model_name,
                local_files_only=local_only,
            )
            self._model.eval()
            texts = [
                f"知识库：{entry.scope}\n问题：{entry.question}\n答案：{entry.answer}"
                for entry in self.entries
            ]
            self._entry_embeddings = self._encode(texts)
        except Exception as exc:
            self._disabled = True
            self._model = None
            self._tokenizer = None
            logger.warning(
                "[KB][QA] Semantic retrieval disabled: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

        logger.info(
            "[KB][QA] Semantic index ready model=%s entries=%d elapsed_ms=%.1f",
            self.model_name,
            len(self.entries),
            (time.perf_counter() - started) * 1000,
        )
        return True

    def _encode(self, texts: list[str]):
        import numpy as np
        import torch

        batches = []
        try:
            batch_size = max(1, int(os.getenv("KB_QA_EMBEDDING_BATCH_SIZE", "32")))
        except ValueError:
            batch_size = 32

        for start in range(0, len(texts), batch_size):
            encoded = self._tokenizer(
                texts[start:start + batch_size],
                padding=True,
                truncation=True,
                max_length=256,
                return_tensors="pt",
            )
            with torch.inference_mode():
                vectors = self._model(**encoded).last_hidden_state[:, 0]
                vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
            batches.append(vectors.cpu().numpy().astype("float32", copy=False))
        return np.concatenate(batches, axis=0)


class KnowledgeBase:
    def __init__(self, root: Path):
        self.root = root
        self.chunks: list[_Chunk] = []
        self.document_frequency: dict[str, int] = {}
        self.scope_terms: dict[str, frozenset[str]] = {}
        self.scope_frequency: dict[str, int] = {}
        self.qa_entries: list[_QaEntry] = []
        self.qa_document_frequency: dict[str, int] = {}
        self._load()
        self.semantic_qa_index = _LocalSemanticQaIndex(self.qa_entries)

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
            if scope in _PRIORITY_QA_SCOPES:
                self.qa_entries.extend(self._parse_qa_entries(scope, title, text))
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

        for entry in self.qa_entries:
            for term in (
                set(entry.question_terms)
                | set(entry.answer_terms)
                | set(entry.source_terms)
            ):
                self.qa_document_frequency[term] = (
                    self.qa_document_frequency.get(term, 0) + 1
                )

        logger.info(
            "[KB] Loaded %d files into %d chunks and %d priority QA entries from %s",
            len(files), len(self.chunks), len(self.qa_entries), self.root,
        )

    @staticmethod
    def _parse_qa_entries(scope: str, title: str, text: str) -> list[_QaEntry]:
        entries: list[_QaEntry] = []
        question = ""
        answer_lines: list[str] = []
        reading_answer = False

        def flush() -> None:
            nonlocal question, answer_lines, reading_answer
            answer = "\n".join(answer_lines).strip()
            if question and answer:
                entries.append(
                    _QaEntry(
                        scope=scope,
                        title=title,
                        question=question,
                        answer=answer,
                        question_terms=frozenset(_terms(question, query=True)),
                        answer_terms=frozenset(_terms(answer, query=True)),
                        source_terms=frozenset(_terms(scope, query=True)),
                    )
                )
            question = ""
            answer_lines = []
            reading_answer = False

        for raw_line in text.splitlines():
            question_match = _QA_QUESTION_RE.match(raw_line)
            if question_match:
                flush()
                question = question_match.group(1).strip()
                continue

            answer_match = _QA_ANSWER_RE.match(raw_line)
            if answer_match and question:
                reading_answer = True
                first_line = answer_match.group(1).strip()
                if first_line:
                    answer_lines.append(first_line)
                continue

            if reading_answer:
                if re.match(r"^\s*#{1,3}\s+", raw_line):
                    flush()
                    continue
                answer_lines.append(raw_line.rstrip())

        flush()
        return entries

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

    def warm(self) -> None:
        self.semantic_qa_index.warm()

    def _search_priority_qa(
        self,
        query: str,
        query_counts: Counter[str],
    ) -> KnowledgeMatch | None:
        if not self.qa_entries:
            return None

        try:
            min_relevance = min(
                1.0,
                max(0.0, float(os.getenv("KB_QA_MIN_RELEVANCE", "0.07"))),
            )
        except ValueError:
            min_relevance = 0.07

        query_terms = set(query_counts)
        entry_count = len(self.qa_entries)

        def idf(term: str) -> float:
            frequency = self.qa_document_frequency.get(term, 0)
            return math.log((entry_count + 1) / (frequency + 0.5)) + 1.0

        query_weight = sum(idf(term) for term in query_terms)
        normalized_query = _normalize_text(query)
        lexical_candidates: list[tuple[int, KnowledgeMatch]] = []

        for entry_index, entry in enumerate(self.qa_entries):
            question_terms = set(entry.question_terms)
            answer_terms = set(entry.answer_terms)
            source_terms = set(entry.source_terms)
            searchable_terms = question_terms | answer_terms | source_terms
            overlap = query_terms & searchable_terms
            if not overlap:
                continue

            question_overlap = overlap & question_terms
            answer_overlap = overlap & answer_terms
            if (
                len(overlap) < 2
                and not any(len(term) >= 3 for term in overlap)
            ):
                continue

            weighted_overlap = sum(
                idf(term)
                * (
                    1.0 if term in question_terms
                    else 0.55 if term in answer_terms
                    else 0.20
                )
                for term in overlap
            )
            question_weight = sum(idf(term) for term in question_terms)
            answer_weight = sum(idf(term) for term in answer_terms)
            source_weight = sum(idf(term) for term in source_terms)
            query_coverage = weighted_overlap / query_weight if query_weight else 0.0
            question_coverage = (
                sum(idf(term) for term in question_overlap) / question_weight
                if question_weight else 0.0
            )
            answer_coverage = (
                sum(idf(term) for term in answer_overlap) / answer_weight
                if answer_weight else 0.0
            )
            source_overlap = overlap & source_terms
            source_coverage = (
                sum(idf(term) for term in source_overlap) / source_weight
                if source_weight else 0.0
            )
            role_overlap = query_terms & _QA_ROLE_TERMS & searchable_terms
            role_bonus = min(0.24, len(role_overlap) * 0.12)
            normalized_question = _normalize_text(entry.question)
            exact_or_contained = (
                len(normalized_query) >= 3
                and (
                    normalized_query in normalized_question
                    or normalized_question in normalized_query
                )
            )
            relevance = (
                (0.45 * query_coverage)
                + (0.30 * question_coverage)
                + (0.15 * answer_coverage)
                + (0.10 * source_coverage)
                + role_bonus
                + (0.35 if exact_or_contained else 0.0)
            )
            if relevance < min_relevance:
                continue

            lexical_candidates.append(
                (
                    entry_index,
                    KnowledgeMatch(
                        title=entry.title,
                        text=entry.answer,
                        score=relevance * 100,
                        coverage=query_coverage,
                        matched_terms=tuple(
                            sorted(overlap, key=lambda term: (-len(term), term))[:12]
                        ),
                        kind="qa",
                        standard_question=entry.question,
                        standard_answer=entry.answer,
                    ),
                )
            )

        lexical_candidates.sort(
            key=lambda item: (-item[1].score, -item[1].coverage, item[1].title)
        )
        if lexical_candidates:
            best = lexical_candidates[0][1]
            runner_up_score = (
                lexical_candidates[1][1].score if len(lexical_candidates) > 1 else 0.0
            )
            try:
                strong_relevance = float(
                    os.getenv("KB_QA_LEXICAL_STRONG_RELEVANCE", "0.28")
                )
            except ValueError:
                strong_relevance = 0.28
            if (
                best.score >= strong_relevance * 100
                and best.score >= runner_up_score * 1.08
            ):
                return best

        semantic_scores = self.semantic_qa_index.scores(query)
        if semantic_scores is None:
            return lexical_candidates[0][1] if lexical_candidates else None

        try:
            semantic_threshold = min(
                1.0,
                max(0.0, float(os.getenv("KB_QA_SEMANTIC_THRESHOLD", "0.52"))),
            )
        except ValueError:
            semantic_threshold = 0.52

        lexical_by_index = {
            entry_index: match for entry_index, match in lexical_candidates
        }
        hybrid_candidates: list[KnowledgeMatch] = []
        for entry_index, semantic_score in enumerate(semantic_scores):
            lexical_match = lexical_by_index.get(entry_index)
            lexical_relevance = (lexical_match.score / 100) if lexical_match else 0.0
            if semantic_score < semantic_threshold and lexical_match is None:
                continue
            entry = self.qa_entries[entry_index]
            combined_score = (
                (0.62 * semantic_score)
                + (0.38 * min(1.0, lexical_relevance))
            )
            hybrid_candidates.append(
                KnowledgeMatch(
                    title=entry.title,
                    text=entry.answer,
                    score=combined_score * 100,
                    coverage=lexical_match.coverage if lexical_match else 0.0,
                    matched_terms=(
                        lexical_match.matched_terms if lexical_match else ("semantic",)
                    ),
                    kind="qa",
                    standard_question=entry.question,
                    standard_answer=entry.answer,
                    semantic_score=semantic_score,
                )
            )

        if not hybrid_candidates:
            return None
        hybrid_candidates.sort(
            key=lambda item: (
                -item.score,
                -item.semantic_score,
                -item.coverage,
                item.title,
            )
        )
        return hybrid_candidates[0]

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

        qa_match = self._search_priority_qa(query, _qa_query_terms(query))
        if qa_match is not None:
            return KnowledgeSearchResult(query=query, matches=(qa_match,))

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
            f"{match.title} kind={match.kind} score={match.score:.2f} "
            f"semantic={match.semantic_score:.3f} coverage={match.coverage:.0%} "
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


def warm_knowledge_base() -> None:
    """Load the configured QA files and semantic index before serving users."""
    global _kb
    root_value = os.getenv("KNOWLEDGE_BASE_DIR", "").strip()
    if not root_value:
        return
    with _kb_lock:
        if _kb is None or str(_kb.root) != root_value:
            _kb = KnowledgeBase(Path(root_value).expanduser())
        knowledge_base = _kb
    knowledge_base.warm()


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
