"""Resolve editable local strategy and prompt documents at runtime.

The files under ``knowledge_sources`` are deliberately kept as normal text
documents. This module is the small bridge between those documents and the
runtime prompt builders, so content changes do not require copying prompt
strings into Python again.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml


_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_SOURCES_ROOT = _PROJECT_ROOT / "knowledge_sources"


def configured_sources_root() -> Path:
    configured = os.getenv("KNOWLEDGE_SOURCES_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_SOURCES_ROOT


def local_strategy_root() -> Path:
    return configured_sources_root() / "03_digital_employee_strategy"


def local_product_root() -> Path:
    return configured_sources_root() / "02_aisales_product_knowledge"


def editable_prompts_root() -> Path:
    return configured_sources_root() / "04_editable_prompts"


def read_editable_prompt(filename: str, fallback: str = "") -> str:
    """Read one editable prompt, returning the fallback if unavailable."""
    path = (editable_prompts_root() / filename).resolve()
    try:
        path.relative_to(editable_prompts_root().resolve())
    except ValueError:
        return fallback.strip()
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or fallback.strip()
    except (OSError, UnicodeError):
        return fallback.strip()


def prompt_body(filename: str, fallback: str = "") -> str:
    """Return the user-editable body without maintenance instructions.

    The source files intentionally contain a small ``待你校订`` footer so a
    human knows where to edit them. That footer is useful to editors but must
    not be sent to the model. Runtime-only sections can also be excluded by
    placing them after the named heading.
    """
    text = read_editable_prompt(filename, fallback)
    if not text:
        return ""
    lines = text.splitlines()
    body: list[str] = []
    for line in lines:
        heading = line.strip()
        if heading in {"## 待你校订", "## 运行时附加内容"}:
            break
        if not body and heading.startswith("# "):
            continue
        body.append(line)
    return "\n".join(body).strip()


def reply_templates() -> dict:
    """Load the YAML block inside the editable Markdown reply document."""
    text = read_editable_prompt("06_deterministic_reply_templates.md")
    match = re.search(r"^```yaml\s*\n(.*?)^```\s*$", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("固定回复文档缺少 yaml 代码块")
    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("固定回复模板必须是对象")
    required = {
        "voice_chat", "school_price", "growth_report", "personal_indicator",
        "unclear_speech", "unconfirmed_delivery", "material_send", "personal_operation",
        "boundary_motive", "boundary_score", "boundary_review", "boundary_teacher",
        "boundary_decision", "boundary_exam", "boundary_default",
    }
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        raise ValueError("固定回复模板缺少必需的非空话术")
    rate = data.get("annual_student_rate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
        raise ValueError("annual_student_rate 必须为正数")
    # Validate placeholders before a real conversation reaches the template.
    data["school_price"].format(student_count=1000, annual_price="2.5万元", rate=rate)
    return data

