"""Local, evidence-oriented knowledge-base retrieval for voice Q&A."""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from aisales_strategy import configured_ai_sales_product_root
from utils.logger import logger


_CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
_DEFAULT_MAX_CHARS = 1200
_DEFAULT_CONTEXT_CHARS = 6000
_BUNDLED_KNOWLEDGE_ROOT = Path(__file__).resolve().parent / "knowledge"
_PRIORITY_QA_SCOPES = {
    "AI素养评价产品QA",
    "教师发展评价系统常见场景QA",
    "AI宣传招生就业系统常见QA",
}
_AI_LITERACY_QA_SCOPE = "AI素养评价产品QA"
_TEACHER_DEVELOPMENT_QA_SCOPE = "教师发展评价系统常见场景QA"
_ADMISSIONS_EMPLOYMENT_QA_SCOPE = "AI宣传招生就业系统常见QA"
_DOMAIN_QUERY_RE = re.compile(
    r"AI|素养|评价|学生|孩子|教师|老师|学校|家长|考生|招生|就业|"
    r"系统|产品|班主任|班级|年级|五育|德育|体育|美育|劳育|成长|档案|报告|教研|"
    r"劳动|运动会|家务|奖状|体质|职称|简历|岗位|招聘|面试|一体机|"
    r"宣传屏|开放日|专业介绍|数字人",
    re.IGNORECASE,
)
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
_COMPANY_PROFILE_QUERY_RE = re.compile(
    r"(哪家|什么|哪个公司|你们|贵).{0,5}公司|"
    r"公司.{0,8}(背景|介绍|简介|成立|创立|总部|地址|规模|团队|资质|荣誉)|"
    r"(介绍|讲讲|说说|了解).{0,8}(你们|贵)?公司|"
    r"(成立|创立).{0,6}(多久|哪年|时间)|总部.{0,5}(哪里|在哪|地址)"
)
_QA_ROLE_TERMS = {
    "家长", "考生", "学生", "老师", "教师", "校长", "管理者",
    "教研", "教务", "招生", "就业", "班主任", "信息化",
}
_QA_QUERY_EXPANSIONS = (
    (
        re.compile(r"什么是\s*AI\s*素养评价|AI\s*素养评价.{0,6}(什么|干嘛|作用)", re.I),
        "这个系统主要是做什么 学生综合素质评价",
    ),
    (re.compile(r"孩子|小孩"), "学生"),
    (re.compile(r"闲置|吃灰|没什么用"), "摆设 招生季"),
    (re.compile(r"乱说|说错|答错"), "回答不准确"),
    (re.compile(r"求职材料"), "简历"),
    (re.compile(r"小范围.{0,4}(试|用)"), "试点"),
    (re.compile(r"忙不过来|接待不过来"), "人流量大 现场人很多"),
    (
        re.compile(
            r"考试成绩|哪些方面.{0,4}(表现|成长)|记录.{0,6}表现|"
            r"体育.{0,8}(劳动|实践)|劳动.{0,8}(体育|实践)"
        ),
        "五个维度 德智体美劳 评价采集内容",
    ),
    (
        re.compile(r"语音.{0,8}(评价|记录|录入)|评价记录"),
        "AI自动采集数据 老师手动输入",
    ),
    (
        re.compile(r"(说句话|说一句|口述).{0,10}(评价|记录|录入)|把评价.{0,6}录进去"),
        "AI如何自动采集数据 老师手动输入",
    ),
    (
        re.compile(r"评价标准.{0,10}(不同|适配|学校)|不同学校.{0,8}评价"),
        "系统适用学校 自定义评价指标 适配学校办学特色",
    ),
    (
        re.compile(r"(孩子|学生).{0,10}(学期|成长).{0,8}报告"),
        "评价结果 学期成长报告",
    ),
    (
        re.compile(
            r"(学生)?成长报告.{0,8}(怎么|如何).{0,6}(生成|形成|制作)|"
            r"(怎么|如何).{0,6}(生成|形成|制作).{0,8}(学生)?成长报告"
        ),
        "AI如何自动采集数据 评价结果 自动生成 学期成长报告",
    ),
    (
        re.compile(r"(学期|期末).{0,10}(形成|得到|看到|拿到).{0,8}(材料|结果|总结)"),
        "评价结果 自动生成 学期成长报告",
    ),
    (
        re.compile(
            r"(期末|学期末).{0,12}(汇总|统计|整理|手写)|"
            r"(汇总|统计|整理|手写).{0,12}(期末|学期末)|"
            r"素质.{0,5}(手册|报告).{0,8}(手写|汇总)"
        ),
        "评价结果 自动生成 学期成长报告 教师记录 减负效果",
    ),
    (
        re.compile(
            r"老师.{0,12}记录.{0,12}学生.{0,12}(负担|工作量|更累)|"
            r"记录.{0,12}学生.{0,12}(负担|工作量|更累)|"
            r"老师.{0,8}给学生.{0,8}(记录|评价).{0,8}(负担|工作量|更累)"
        ),
        "AI素养评价 教师记录 减负效果",
    ),
    (
        re.compile(
            r"(学生评价|学生成长|学生综合).{0,10}(上线|部署|配置|正式用).{0,8}"
            r"(麻烦|多久|时间|正式用)|从配置.{0,8}正式用"
        ),
        "从部署到上线需要多长时间",
    ),
    (
        re.compile(r"(老师|教师).{0,8}(成长报告|个人报告)"),
        "系统能生成教师个人成长报告",
    ),
    (
        re.compile(r"(给|对).{0,4}(老师|教师).{0,6}(排名|考核|优劣)"),
        "系统是不是用来考核老师",
    ),
    (
        re.compile(r"校长.{0,8}老师.{0,8}家长|家长.{0,8}老师.{0,8}校长"),
        "系统有哪些角色权限 如何管理",
    ),
    (
        re.compile(r"综合素质评价.{0,8}(好处|价值|效果|作用)"),
        "使用这个系统能带来什么实际效果",
    ),
    (
        re.compile(r"小学|初中|高中|中小学|哪类学校|什么学校.{0,4}(能用|适合)"),
        "系统适用于哪类学校",
    ),
    (
        re.compile(r"国家政策|政策导向|教育评价改革|五育并举"),
        "系统符合国家政策导向",
    ),
    (
        re.compile(r"(已有|现有).{0,6}(教务|德育).{0,4}系统|教务系统|德育系统"),
        "学校已经有教务系统 系统能对接",
    ),
    (
        re.compile(r"拍照|奖状|家务劳动|课堂表现.{0,6}(采集|记录)|活动参与记录"),
        "AI自动采集数据 老师手动输入",
    ),
    (
        re.compile(r"客观|公正|主观偏差|评价偏差|凭印象"),
        "保证评价客观公正 避免主观偏差",
    ),
    (
        re.compile(r"角色权限|谁能看|谁可以看|不同角色|权限管理"),
        "系统有哪些角色权限 如何管理",
    ),
    (
        re.compile(r"操作.{0,4}(难|复杂)|上手|培训多久|学多久"),
        "系统操作复杂 老师培训多久",
    ),
    (
        re.compile(r"技术支持|售后|运维|出了问题.{0,4}(找谁|怎么办)"),
        "系统上线 技术支持保障",
    ),
    (
        re.compile(r"(班级|年级|几个班|几个年级).{0,8}(试点|先试|试用)|先试.{0,5}(班|年级)"),
        "部署上线 选择一到两个年级试点运行",
    ),
    (
        re.compile(
            r"[一二三四五六七八九十两0-9]+个班.{0,8}(试试|先试|试点|试用)|"
            r"先拿.{0,6}个班.{0,6}(试试|先试|试点|试用)"
        ),
        "部署上线 选择一到两个年级试点运行",
    ),
    (
        re.compile(r"普通.{0,4}教师档案|和教师档案.{0,4}区别"),
        "普通教师档案系统有什么区别",
    ),
    (
        re.compile(r"(教师|老师).{0,5}(有什么帮助|个人帮助|得到什么|有啥用)"),
        "系统对老师个人有什么帮助",
    ),
    (
        re.compile(r"骨干梯队|教师梯队|教师队伍建设|师资队伍"),
        "系统支撑教师队伍建设",
    ),
    (
        re.compile(r"(建设|使用).{0,5}(成效|效果).{0,5}(判断|衡量|评估)"),
        "系统建设成效怎么判断",
    ),
    (
        re.compile(r"教师.{0,5}(材料|成果|培训|师德|教研).{0,5}(数据|记录|接入)|"
                   r"接入哪些教师数据"),
        "系统需要接入哪些数据",
    ),
    (
        re.compile(r"人事系统|人事.{0,5}(平台|数据).{0,5}(对接|接入)"),
        "学校已有系统对接 人事 教务 科研 培训",
    ),
    (
        re.compile(r"评价数据.{0,5}(滥用|乱用|挪用)|避免.{0,5}数据滥用"),
        "避免评价数据被滥用",
    ),
    (
        re.compile(r"一核三库五应用|一核.{0,4}三库.{0,4}五应用"),
        "解释一核三库五应用",
    ),
    (
        re.compile(r"学校宣传.{0,5}(口径|说法).{0,5}(统一|一致)|口径不统一"),
        "保证学校宣传口径统一",
    ),
    (
        re.compile(
            r"招生宣传.{0,8}(毕业生)?就业.{0,8}(一起|一体|放在一起)|"
            r"招生.{0,5}就业.{0,5}(一起|一体)"
        ),
        "为什么宣传招生和就业服务要放在一套系统",
    ),
    (
        re.compile(r"智慧校园|校园数字化建设"),
        "这套系统和智慧校园建设有什么关系",
    ),
    (
        re.compile(r"(建设|建成|完成).{0,8}(看得见|直观).{0,6}(效果|变化|改变)"),
        "系统建成后能带来哪些看得见的效果",
    ),
    (
        re.compile(r"(买回来|买来|放着).{0,8}(摆设|闲置|吃灰)|变成.{0,4}摆设"),
        "系统会不会变成一个摆设",
    ),
    (
        re.compile(r"招生(办)?老师.{0,8}(减轻|减少|变化|工作)"),
        "招生老师最能感受到的变化是什么",
    ),
    (
        re.compile(r"(家长|考生).{0,5}(意向|联系方式|电话).{0,5}(记录|登记|留下)"),
        "记录家长和考生意向",
    ),
    (
        re.compile(r"职业规划|专业方向|未来职业"),
        "系统能不能做职业规划",
    ),
    (
        re.compile(r"面试.{0,5}(准备|辅导|建议|模拟)"),
        "系统帮我准备面试",
    ),
    (
        re.compile(r"(润色|修改|优化).{0,8}(求职)?简历|求职简历"),
        "系统能不能帮学生改简历",
    ),
    (
        re.compile(r"(特别|非常|比较)?复杂.{0,5}(就业|求职).{0,5}(问题|诉求|情况)"),
        "复杂就业问题怎么处理",
    ),
    (
        re.compile(
            r"上线前.{0,8}(整理|提供|准备).{0,8}(内容|资料)|"
            r"学校.{0,6}准备哪些资料"
        ),
        "学校需要准备哪些资料",
    ),
    (
        re.compile(r"资料.{0,5}(更新|修改).{0,6}(同步|回答|生效)"),
        "资料更新 系统回答同步变化",
    ),
    (
        re.compile(r"(隐私|信息|数据).{0,5}(泄露|外泄)|保护.{0,5}(家长|学生).{0,3}信息"),
        "系统会不会泄露学生或家长信息",
    ),
    (
        re.compile(r"(线下设备|一体机).{0,8}(维护|维修).{0,6}(麻烦|费事|难|成本)"),
        "线下一体机后期维护麻烦吗",
    ),
    (
        re.compile(
            r"招生老师.{0,8}(回电|回电话|联系).{0,8}(登记|记录|留下|可以)|"
            r"请招生老师.{0,6}(联系|回电|回电话)"
        ),
        "如果我想让老师联系我 系统能记录吗",
    ),
    (
        re.compile(r"开放日.{0,8}(高峰|人多|人流).{0,8}(分担|承接|接待|多少)"),
        "开放日人流量大 系统能承接多少工作",
    ),
)

_QA_SCOPE_CUES = {
    _AI_LITERACY_QA_SCOPE: (
        (re.compile(r"AI\s*素养评价|素养评价|综合素养评价|综合素质评价", re.I), 6),
        (re.compile(r"五育|德智体美劳|成长银行|校本指标|评价标准"), 4),
        (
            re.compile(
                r"(老师|教师).{0,12}(说一句|说话|口述|语音|平板).{0,12}"
                r"(学生|孩子)|"
                r"(学生|孩子).{0,12}(表现|行为|成长).{0,12}(记录|评价|录入)"
            ),
            5,
        ),
        (
            re.compile(
                r"学生表现|学生.{0,6}(成长|评价|档案|报告)|"
                r"孩子.{0,8}(成长|评价|档案|报告)|"
                r"评价.{0,4}学生|考核学生|考试成绩|课堂表现|"
                r"体育比赛|劳动教育|社会实践|成长档案"
            ),
            3,
        ),
        (
            re.compile(
                r"小学|初中|高中|中小学|教务系统|德育系统|学生报告|"
                r"家务劳动|做家务|运动会|奖状|体质测试|班级试点|年级试点|"
                r"几个班.{0,5}(先试|试点|试用)|先试.{0,5}几个班|"
                r"[一二三四五六七八九十两0-9]+个班.{0,5}(试试|先试|试点|试用)|"
                r"主观偏差|客观公正|评价.{0,4}凭印象"
            ),
            3,
        ),
        (
            re.compile(
                r"评价记录|家校互动|家校协同|"
                r"家长.{0,6}(孩子|成长档案)|"
                r"语音.{0,8}(记录|评价)|"
                r"老师.{0,10}(记录|录入).{0,10}(学生|表现|评价)|"
                r"(说句话|说一句|口述).{0,8}(评价|记录|录入)|"
                r"老师.{0,8}给学生.{0,8}(记录|评价)|"
                r"校长.{0,8}老师.{0,8}家长|评价.{0,5}(凭老师印象|全凭印象)"
            ),
            2,
        ),
    ),
    _TEACHER_DEVELOPMENT_QA_SCOPE: (
        (re.compile(r"教师发展评价|教师发展系统|教师评价系统|老师评价系统"), 6),
        (
            re.compile(
                r"教师队伍|教师成长|教师档案|教师个人|老师个人|"
                r"评价老师|考核老师|老师.{0,6}(考核|评价依据|排名|优劣)"
            ),
            4,
        ),
        (
            re.compile(
                r"评优评先|评职称|职称推荐|青年教师|教研组|"
                r"青年老师|校本研修|一核三库五应用|骨干教师|骨干梯队|"
                r"教师画像|发展画像|师资队伍|人事系统|人事平台"
            ),
            4,
        ),
        (re.compile(r"老师使用系统.{0,8}(工作量|负担)"), 2),
    ),
    _ADMISSIONS_EMPLOYMENT_QA_SCOPE: (
        (re.compile(r"宣传招生|招生就业|AI\s*招生就业", re.I), 6),
        (
            re.compile(
                r"招生|就业|考生|专业选择|职业规划|简历|岗位|招聘|"
                r"面试|开放日|宣传屏|一体机|三方协议|档案转接|求职|"
                r"招生政策|校园导览|意向登记|智慧校园|专业介绍|"
                r"数字人.{0,8}(资料|内容|回答|更新)|资料.{0,8}数字人"
            ),
            4,
        ),
    ),
}

_QA_DIRECT_INTENTS = (
    (
        re.compile(
            r"(期末|学期末).{0,12}(汇总|统计|整理|手写)|"
            r"(汇总|统计|整理|手写).{0,12}(期末|学期末)|"
            r"素质.{0,5}(手册|报告).{0,8}(手写|汇总)"
        ),
        _AI_LITERACY_QA_SCOPE,
        "评价结果会自动生成哪些报告",
    ),
    (
        re.compile(r"评价.{0,8}(全凭|只凭|光凭).{0,5}(老师)?印象|凭印象.{0,6}评价"),
        _AI_LITERACY_QA_SCOPE,
        "如何保证评价的客观公正",
    ),
    (
        re.compile(
            r"[一二三四五六七八九十两0-9]+个班.{0,8}(试试|先试|试点|试用)|"
            r"先拿.{0,6}个班.{0,6}(试试|先试|试点|试用)"
        ),
        _AI_LITERACY_QA_SCOPE,
        "从部署到上线需要多长时间",
    ),
    (
        re.compile(r"人事.{0,6}(教务)?(系统|平台).{0,6}(接|对接|接入)|人事和教务平台"),
        _TEACHER_DEVELOPMENT_QA_SCOPE,
        "能否和学校已有系统对接",
    ),
    (
        re.compile(r"招生就业.{0,8}(平台|系统)?.{0,8}(主要解决|解决学校|解决校方)"),
        _ADMISSIONS_EMPLOYMENT_QA_SCOPE,
        "这套系统主要解决学校什么问题",
    ),
    (
        re.compile(r"开放日.{0,8}(人挤人|人多|拥挤|接待不过来).{0,8}(分担|接待|怎么办|做什么)?"),
        _ADMISSIONS_EMPLOYMENT_QA_SCOPE,
        "开放日现场人很多",
    ),
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _qa_query_terms(query: str) -> Counter[str]:
    counts = _terms(query, query=True)
    for pattern, expansion in _QA_QUERY_EXPANSIONS:
        if pattern.search(query or ""):
            counts.update(_terms(expansion, query=True))
    return counts


def _priority_qa_scopes(query: str) -> set[str]:
    """Return strongly implied product scopes for priority QA matching."""
    scores: dict[str, int] = {}
    for scope, cues in _QA_SCOPE_CUES.items():
        score = sum(weight for pattern, weight in cues if pattern.search(query or ""))
        if score:
            scores[scope] = score
    if not scores:
        return set()
    best_score = max(scores.values())
    return {
        scope
        for scope, score in scores.items()
        if score == best_score or (best_score >= 6 and score >= best_score - 1)
    }


def _priority_qa_intent(query: str) -> tuple[str, str] | None:
    for pattern, scope, question_fragment in _QA_DIRECT_INTENTS:
        if pattern.search(query or ""):
            return scope, question_fragment
    return None


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
    scope: str = ""
    documents: tuple["KnowledgeDocument", ...] = ()

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

    def scope_context(self, max_chars: int = 50000) -> str:
        """Return stable, full-document context for the selected product scope."""
        blocks: list[str] = []
        used = 0
        for index, document in enumerate(self.documents, start=1):
            block = (
                f"[文档 {index}]\n"
                f"文档路径：{document.title}\n"
                f"原文：\n{document.text}"
            )
            if used + len(block) > max_chars:
                break
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)


@dataclass(frozen=True)
class KnowledgeDocument:
    scope: str
    title: str
    text: str


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
    def __init__(self, root: Path, additional_roots: tuple[Path, ...] = ()):
        roots: list[Path] = []
        seen_roots: set[Path] = set()
        for candidate in (root, *additional_roots):
            resolved = candidate.expanduser().resolve()
            if resolved in seen_roots:
                continue
            seen_roots.add(resolved)
            roots.append(resolved)

        self.root = roots[0]
        self.roots = tuple(roots)
        self.chunks: list[_Chunk] = []
        self.document_frequency: dict[str, int] = {}
        self.scope_terms: dict[str, frozenset[str]] = {}
        self.scope_frequency: dict[str, int] = {}
        self.qa_entries: list[_QaEntry] = []
        self.qa_document_frequency: dict[str, int] = {}
        self.documents: list[KnowledgeDocument] = []
        self._load()
        self.semantic_qa_index = _LocalSemanticQaIndex(self.qa_entries)

    @staticmethod
    def _canonical_scope(title: str) -> str:
        source_scope = title.split("/", 1)[0]
        if "宣传招生就业" in source_scope or "招生系统" in source_scope:
            return _ADMISSIONS_EMPLOYMENT_QA_SCOPE
        if "教师发展评价" in source_scope:
            return _TEACHER_DEVELOPMENT_QA_SCOPE
        if "AI素养评价" in source_scope or "学生综合素质评价" in source_scope:
            return _AI_LITERACY_QA_SCOPE
        return source_scope

    def _load(self) -> None:
        loaded_files = 0
        seen_documents: set[tuple[str, str]] = set()
        for root in self.roots:
            if not root.is_dir():
                logger.warning("[KB] Directory not found: %s", root)
                continue

            files = sorted(
                path for path in root.rglob("*")
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
                title = path.relative_to(root).with_suffix("").as_posix()
                document_key = (title, text)
                if document_key in seen_documents:
                    continue
                seen_documents.add(document_key)
                loaded_files += 1

                source_scope = title.split("/", 1)[0]
                scope = self._canonical_scope(title)
                self.documents.append(KnowledgeDocument(scope, title, text))
                title_terms = frozenset(_terms(title))
                self.scope_terms.setdefault(scope, frozenset(_terms(scope)))
                if source_scope in _PRIORITY_QA_SCOPES:
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
            loaded_files, len(self.chunks), len(self.qa_entries),
            ", ".join(str(root) for root in self.roots),
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
        if _COMPANY_PROFILE_QUERY_RE.search(normalized):
            return "strategy_profile"
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

    @staticmethod
    def _configured_default_scope() -> str:
        configured = os.getenv(
            "KB_DEFAULT_SCOPE",
            _AI_LITERACY_QA_SCOPE,
        ).strip()
        aliases = {
            "ai素养评价": _AI_LITERACY_QA_SCOPE,
            "学生综合素质评价": _AI_LITERACY_QA_SCOPE,
            "教师发展评价": _TEACHER_DEVELOPMENT_QA_SCOPE,
            "宣传招生就业": _ADMISSIONS_EMPLOYMENT_QA_SCOPE,
            "招生就业": _ADMISSIONS_EMPLOYMENT_QA_SCOPE,
        }
        return aliases.get(configured.lower(), configured)

    def _resolve_scope(
        self,
        query: str,
        result: KnowledgeSearchResult,
        preferred_scope: str = "",
    ) -> str:
        direct_intent = _priority_qa_intent(query)
        if direct_intent is not None:
            return direct_intent[0]

        explicit_scopes = _priority_qa_scopes(query)
        if len(explicit_scopes) == 1:
            return next(iter(explicit_scopes))

        if (
            preferred_scope in _PRIORITY_QA_SCOPES
            and any(doc.scope == preferred_scope for doc in self.documents)
        ):
            return preferred_scope

        if result.matches:
            best_match = result.matches[0]
            matched_scope = self._canonical_scope(best_match.title)
            reliable_match = (
                best_match.kind == "qa"
                and (
                    best_match.score >= 45.0
                    or best_match.semantic_score >= 0.60
                    or "intent" in best_match.matched_terms
                )
            )
            if reliable_match and matched_scope in _PRIORITY_QA_SCOPES:
                return matched_scope

        inferred_scopes = self._select_scopes(set(_terms(query, query=True)))
        product_scopes = inferred_scopes & _PRIORITY_QA_SCOPES
        if len(product_scopes) == 1:
            return next(iter(product_scopes))

        if _DOMAIN_QUERY_RE.search(query or ""):
            default_scope = self._configured_default_scope()
            if any(doc.scope == default_scope for doc in self.documents):
                return default_scope

        if result.matches:
            matched_scope = self._canonical_scope(result.matches[0].title)
            if matched_scope in _PRIORITY_QA_SCOPES:
                return matched_scope
        return ""

    def search_with_scope_documents(
        self,
        query: str,
        top_k: int = 4,
        preferred_scope: str = "",
    ) -> KnowledgeSearchResult:
        """Search and attach every document in the selected product scope."""
        result = self.search(query, top_k=top_k)
        if result.skipped_reason in {
            "empty_query",
            "casual_conversation",
            "knowledge_base_feedback",
            "strategy_profile",
        }:
            return result

        scope = self._resolve_scope(query, result, preferred_scope=preferred_scope)
        if not scope:
            return result

        shared_matches = tuple(
            match
            for match in result.matches
            if self._canonical_scope(match.title) not in _PRIORITY_QA_SCOPES
        )
        shared_titles = {match.title for match in shared_matches}
        scoped_documents = [
            doc
            for doc in self.documents
            if doc.scope == scope or doc.title in shared_titles
        ]
        scoped_documents.sort(
            key=lambda doc: (
                0 if doc.title.split("/", 1)[0] == scope else 1,
                doc.title,
            )
        )
        scoped_matches = tuple(
            match
            for match in result.matches
            if (
                self._canonical_scope(match.title) == scope
                or match.title in shared_titles
            )
        )
        return replace(
            result,
            matches=scoped_matches,
            scope=scope,
            documents=tuple(scoped_documents),
        )

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
        selected_scopes = _priority_qa_scopes(query)
        direct_intent = _priority_qa_intent(query)
        if direct_intent is not None:
            intent_scope, question_fragment = direct_intent
            for entry in self.qa_entries:
                if entry.scope == intent_scope and question_fragment in entry.question:
                    return KnowledgeMatch(
                        title=entry.title,
                        text=entry.answer,
                        score=120.0,
                        coverage=1.0,
                        matched_terms=("intent",),
                        kind="qa",
                        standard_question=entry.question,
                        standard_answer=entry.answer,
                    )

        def idf(term: str) -> float:
            frequency = self.qa_document_frequency.get(term, 0)
            return math.log((entry_count + 1) / (frequency + 0.5)) + 1.0

        query_weight = sum(idf(term) for term in query_terms)
        normalized_query = _normalize_text(query)
        lexical_candidates: list[tuple[int, KnowledgeMatch]] = []

        for entry_index, entry in enumerate(self.qa_entries):
            if selected_scopes and entry.scope not in selected_scopes:
                continue
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
            if not lexical_candidates:
                return None
            best = lexical_candidates[0][1]
            runner_up_score = (
                lexical_candidates[1][1].score if len(lexical_candidates) > 1 else 0.0
            )
            try:
                fallback_relevance = float(
                    os.getenv("KB_QA_LEXICAL_FALLBACK_RELEVANCE", "0.16")
                )
            except ValueError:
                fallback_relevance = 0.16
            if (
                best.score < fallback_relevance * 100
                or (runner_up_score and best.score < runner_up_score * 1.10)
            ):
                return None
            return best

        try:
            semantic_threshold = min(
                1.0,
                max(0.0, float(os.getenv("KB_QA_SEMANTIC_THRESHOLD", "0.60"))),
            )
        except ValueError:
            semantic_threshold = 0.60

        lexical_by_index = {
            entry_index: match for entry_index, match in lexical_candidates
        }
        hybrid_candidates: list[tuple[KnowledgeMatch, float]] = []
        for entry_index, semantic_score in enumerate(semantic_scores):
            entry = self.qa_entries[entry_index]
            if selected_scopes and entry.scope not in selected_scopes:
                continue
            lexical_match = lexical_by_index.get(entry_index)
            lexical_relevance = (lexical_match.score / 100) if lexical_match else 0.0
            if semantic_score < semantic_threshold:
                if lexical_match is None or lexical_relevance < 0.18:
                    continue
            combined_score = (
                (0.62 * semantic_score)
                + (0.38 * min(1.0, lexical_relevance))
            )
            hybrid_candidates.append(
                (
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
                    ),
                    lexical_relevance,
                )
            )

        if not hybrid_candidates:
            return None
        hybrid_candidates.sort(
            key=lambda item: (
                -item[0].score,
                -item[0].semantic_score,
                -item[0].coverage,
                item[0].title,
            )
        )
        best, best_lexical_relevance = hybrid_candidates[0]
        if best_lexical_relevance < 0.08 and len(hybrid_candidates) > 1:
            runner_up = hybrid_candidates[1][0]
            try:
                semantic_margin = max(
                    0.0,
                    float(os.getenv("KB_QA_SEMANTIC_MARGIN", "0.035")),
                )
            except ValueError:
                semantic_margin = 0.035
            if best.semantic_score - runner_up.semantic_score < semantic_margin:
                return None
        return best

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


def _configured_knowledge_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    root_value = os.getenv("KNOWLEDGE_BASE_DIR", "").strip()
    if root_value:
        # An explicit directory is the authoritative knowledge source. Do not
        # merge the legacy bundled or AiSales product directories into it:
        # merging would reintroduce stale documents and internal SOP material.
        return (Path(root_value).expanduser().resolve(),)
    if (
        _BUNDLED_KNOWLEDGE_ROOT.is_dir()
        and any(path.is_file() for path in _BUNDLED_KNOWLEDGE_ROOT.rglob("*"))
    ):
        bundled_root = _BUNDLED_KNOWLEDGE_ROOT.resolve()
        if bundled_root not in roots:
            roots.append(bundled_root)
    ai_sales_product_root = configured_ai_sales_product_root()
    if ai_sales_product_root.is_dir() and ai_sales_product_root not in roots:
        roots.append(ai_sales_product_root)
    return tuple(roots)


def _get_configured_knowledge_base() -> KnowledgeBase | None:
    global _kb
    roots = _configured_knowledge_roots()
    if not roots:
        return None
    with _kb_lock:
        if _kb is None or _kb.roots != roots:
            _kb = KnowledgeBase(roots[0], additional_roots=roots[1:])
        return _kb


def search_knowledge(query: str, preferred_scope: str = "") -> KnowledgeSearchResult:
    """Search the configured local knowledge base and log auditable hits."""
    start = time.perf_counter()
    knowledge_base = _get_configured_knowledge_base()
    if knowledge_base is None:
        result = KnowledgeSearchResult(
            query=query,
            matches=(),
            skipped_reason="not_configured",
        )
        logger.info("[KB] skipped reason=%s query=%r", result.skipped_reason, query)
        return result

    try:
        top_k = max(1, int(os.getenv("KB_TOP_K", "4")))
    except ValueError:
        top_k = 4
    result = knowledge_base.search_with_scope_documents(
        query,
        top_k=top_k,
        preferred_scope=preferred_scope,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    if result.matches:
        hit_summary = "; ".join(
            f"{match.title} kind={match.kind} score={match.score:.2f} "
            f"semantic={match.semantic_score:.3f} coverage={match.coverage:.0%} "
            f"terms={','.join(match.matched_terms)}"
            for match in result.matches
        )
        logger.info(
            "[KB] query=%r scope=%r docs=%d hits=%d elapsed_ms=%.1f | %s",
            query, result.scope, len(result.documents), len(result.matches), elapsed_ms,
            hit_summary,
        )
    else:
        logger.info(
            "[KB] query=%r scope=%r docs=%d no_hit reason=%s elapsed_ms=%.1f",
            query, result.scope, len(result.documents),
            result.skipped_reason or "below_threshold", elapsed_ms,
        )
    return result


def warm_knowledge_base() -> None:
    """Load the configured QA files and semantic index before serving users."""
    knowledge_base = _get_configured_knowledge_base()
    if knowledge_base is None:
        return
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
