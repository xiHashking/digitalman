#!/usr/bin/env python3
"""Evaluate the tablet agent against anonymized AiSales conversation cases."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from knowledge_base import search_knowledge
from llm import llm_response


MARKDOWN_RE = re.compile(r"\*\*|__|```|^\s*#{1,6}\s|^\s*[-*+]\s", re.MULTILINE)
UNAVAILABLE_ACTION_RE = re.compile(
    r"(我|这边)[^。！？]{0,24}(同步|转接|转给|联系|安排|发送|发您|发给|对接|跟进)|"
    r"后续[^。！？]{0,18}(同事|团队|经理)?[^。！？]{0,8}(联系|对接|跟进)|"
    r"稍后.{0,8}(发|发送|联系|安排)"
)


class CaptureAvatar:
    def __init__(self) -> None:
        self.opt = SimpleNamespace(llm_provider="dashscope", llm_model="")
        self.segments: list[str] = []

    def put_msg_txt(self, text: str, _data: dict) -> None:
        self.segments.append(text)


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("评测集必须是 JSON 数组")
    return payload


def evaluate_case(case: dict, *, live: bool) -> dict:
    question = str(case["question"])
    started = time.perf_counter()
    result = search_knowledge(question)
    row = {
        "id": case["id"],
        "source": case["source"],
        "question": question,
        "historical_answer": case.get("historical_answer", ""),
        "historical_assessment": case.get("historical_assessment", ""),
        "expected_scope": case.get("expected_scope", ""),
        "actual_scope": result.scope,
        "top_match": result.matches[0].title if result.matches else "",
        "retrieval_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    failures: list[str] = []
    expected_scope = str(case.get("expected_scope") or "")
    if expected_scope and result.scope != expected_scope:
        failures.append(f"scope: expected {expected_scope}, got {result.scope or '-'}")

    if live:
        avatar = CaptureAvatar()
        answer_started = time.perf_counter()
        llm_response(question, avatar, {})
        answer = "".join(avatar.segments).strip()
        row["current_answer"] = answer
        row["answer_ms"] = round((time.perf_counter() - answer_started) * 1000, 1)
        required = [str(item) for item in case.get("must_include_any", [])]
        forbidden = [str(item) for item in case.get("must_not_include", [])]
        if required and not any(item in answer for item in required):
            failures.append("answer: missing one of " + ", ".join(required))
        for item in forbidden:
            if item and item in answer:
                failures.append(f"answer: forbidden phrase {item}")
        if len(re.sub(r"\s+", "", answer)) > 140:
            failures.append("answer: longer than 140 characters")
        if MARKDOWN_RE.search(answer):
            failures.append("answer: contains Markdown")
        if UNAVAILABLE_ACTION_RE.search(answer):
            failures.append("answer: claims an unavailable action")
        if not answer:
            failures.append("answer: empty")

    row["passed"] = not failures
    row["failures"] = failures
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evals" / "cao_teacher_history_cases.json",
    )
    parser.add_argument("--live", action="store_true", help="调用当前真实模型回答")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.limit > 0:
        cases = cases[: args.limit]
    rows = [evaluate_case(case, live=args.live) for case in cases]
    passed = sum(1 for row in rows if row["passed"])
    summary = {
        "mode": "live" if args.live else "retrieval",
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "rows": rows,
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
