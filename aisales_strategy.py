"""Load the simplified Cao-teacher strategy directly from AiSalesPrompts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from prompt_sources import local_product_root, local_strategy_root, prompt_body


_DEFAULT_STRATEGY_NAME = "ai_sales_education"
_CORE_MAIN_SECTIONS = (
    "AI素养评价产品定位与能力边界（高优先级）",
    "核心人设",
    "自我介绍",
    "回答策略",
    "业务铁律（必须遵守）",
    "典型问题回答原则",
    "输出原则",
    "初始化",
)
_CASE_QUERY_RE = re.compile(r"案例|成功经验|学校.{0,6}(在用|使用)|实际效果")
_TRANSFER_QUERY_RE = re.compile(
    r"价格|报价|收费|多少钱|费用|年费|计费|合作|采购|试点|试用|定制|开发|对接|实施周期"
)
_MATERIAL_QUERY_RE = re.compile(r"资料|PDF|pdf|方案|文档|说明书")
_BC_SPLIT_QUERY_RE = re.compile(
    r"个人|自己用|体验|试用|免费|小程序|学校版|学校采购|单位评估|"
    r"班级试点|老师使用|教师使用"
)
_PRODUCT_DETAIL_RE = re.compile(
    r"系统|产品|功能|报告|评价|学生|教师|老师|家长|学校|五育|"
    r"成长|档案|部署|培训|权限|数据|接口|招生|就业"
)


@dataclass(frozen=True)
class AiSalesPromptDocument:
    title: str
    text: str


@dataclass(frozen=True)
class AiSalesStrategyBundle:
    strategy_name: str
    strategy_root: Path
    product_root: Path
    documents: tuple[AiSalesPromptDocument, ...]

    def context(self) -> str:
        return "\n\n".join(
            f"文档路径：{document.title}\n原文：\n{document.text}"
            for document in self.documents
        )


def configured_ai_sales_worktree() -> Path:
    configured = os.getenv("AISALES_PROMPT_WORKTREE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path(__file__).resolve().parent.parent
        / "org_worktrees"
        / _DEFAULT_STRATEGY_NAME
    ).resolve()


def configured_ai_sales_strategy_name() -> str:
    return os.getenv("AISALES_STRATEGY_NAME", _DEFAULT_STRATEGY_NAME).strip()


def configured_ai_sales_product_root() -> Path:
    worktree = configured_ai_sales_worktree()
    product_name = os.getenv(
        "AISALES_PRODUCT_NAME",
        _DEFAULT_STRATEGY_NAME,
    ).strip()
    return (worktree / "products" / product_name).resolve()


def _resolve_source(base: Path, relative_path: str) -> Path:
    source = (base / relative_path).resolve()
    try:
        source.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"策略文件越出允许目录: {relative_path}") from exc
    return source


def _extract_main_prompt(text: str, question: str = "") -> str:
    lines = text.splitlines()
    selected: list[str] = []
    selected_sections = set(_CORE_MAIN_SECTIONS)
    if _BC_SPLIT_QUERY_RE.search(question or ""):
        selected_sections.add("对话阶段判断")

    first_section = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")),
        len(lines),
    )
    selected.extend(lines[:first_section])

    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,4})\s+(.+?)\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))

    for heading_index, level, title in headings:
        if title not in selected_sections:
            continue
        end = len(lines)
        for next_index, next_level, _ in headings:
            if next_index > heading_index and next_level <= level:
                end = next_index
                break
        selected.extend(("", *lines[heading_index:end]))

    return "\n".join(selected).strip()


def _selected_skill_names(question: str) -> set[str]:
    names = {"basic-product-information-introduction"}
    if _PRODUCT_DETAIL_RE.search(question or ""):
        names.add("product-qa-rule")
    if _CASE_QUERY_RE.search(question or ""):
        names.add("case-introduction")
    if _TRANSFER_QUERY_RE.search(question or ""):
        names.add("transfer-to-human-rule")
    if _MATERIAL_QUERY_RE.search(question or ""):
        names.add("send-material")
    return names


def load_ai_sales_strategy(question: str) -> AiSalesStrategyBundle | None:
    """Load the local editable strategy, with AiSales as an explicit fallback."""
    strategy_name = configured_ai_sales_strategy_name()
    explicit_worktree = os.getenv("AISALES_PROMPT_WORKTREE", "").strip()
    local_root = local_strategy_root()
    if not explicit_worktree and (local_root / "prompt_assembly.yaml").is_file():
        strategy_root = local_root
        product_root = local_product_root()
    else:
        worktree = configured_ai_sales_worktree()
        strategy_root = (worktree / strategy_name).resolve()
        product_root = configured_ai_sales_product_root()
    assembly_path = strategy_root / "prompt_assembly.yaml"
    if not assembly_path.is_file() or not product_root.is_dir():
        return None

    assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8")) or {}
    if not isinstance(assembly, dict):
        raise ValueError("prompt_assembly.yaml 必须是对象")

    cached_prompts = (
        assembly.get("main_agent_prompt", {}).get("cached", [])
        if isinstance(assembly.get("main_agent_prompt"), dict)
        else []
    )
    main_source = next(
        (
            item.get("path")
            for item in cached_prompts
            if isinstance(item, dict) and item.get("path")
        ),
        None,
    )
    if not main_source:
        raise ValueError("曹老师策略缺少 main_agent_prompt.cached.path")

    main_path = _resolve_source(strategy_root, str(main_source))
    documents = [
        AiSalesPromptDocument(
            title=f"数字员工策略/{main_path.relative_to(strategy_root).as_posix()}",
            text=_extract_main_prompt(
                main_path.read_text(encoding="utf-8"),
                question,
            ),
        )
    ]

    selected_skills = _selected_skill_names(question)
    for skill in assembly.get("system_skills", []):
        if not isinstance(skill, dict) or skill.get("name") not in selected_skills:
            continue
        skill_path = _resolve_source(strategy_root, str(skill.get("path", "")))
        documents.append(
            AiSalesPromptDocument(
                title=(
                    "数字员工策略/"
                    f"{skill_path.relative_to(strategy_root).as_posix()}"
                ),
                text=skill_path.read_text(encoding="utf-8").strip(),
            )
        )

    # Company background and answer boundaries are maintained as editable
    # documents beside the strategy. Keep them in the same per-question
    # bundle so the knowledge agent can ground company answers with evidence.
    if strategy_root == local_root:
        for filename in ("03_company_background.md", "04_answer_boundaries.md"):
            text = prompt_body(filename)
            if text:
                documents.append(
                    AiSalesPromptDocument(
                        title=f"可编辑提示词/{filename}",
                        text=text,
                    )
                )

    return AiSalesStrategyBundle(
        strategy_name=strategy_name,
        strategy_root=strategy_root,
        product_root=product_root,
        documents=tuple(documents),
    )
