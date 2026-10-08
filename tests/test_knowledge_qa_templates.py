import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from knowledge_base import (
    KnowledgeBase,
    _ADMISSIONS_EMPLOYMENT_QA_SCOPE,
    _AI_LITERACY_QA_SCOPE,
    _TEACHER_DEVELOPMENT_QA_SCOPE,
    _priority_qa_intent,
    _priority_qa_scopes,
)


class KnowledgeQaTemplateTestCase(unittest.TestCase):
    def setUp(self):
        self.semantic_disabled = patch.dict(
            os.environ,
            {"KB_QA_SEMANTIC_ENABLED": "0"},
        )
        self.semantic_disabled.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)

        student_dir = root / "AI素养评价产品QA"
        student_dir.mkdir()
        (student_dir / "AI素养评价产品QA.md").write_text(
            "# 常见问题\n\n"
            "### Q1：系统能否进行家校互动？\n"
            "**A：** 可以。家长可以查看成长档案，实现家校协同。\n\n"
            "### Q2：老师需要培训多久？\n"
            "**A：** 通常在正式部署后一周内即可熟练掌握。\n\n"
            "### Q3：五个维度能采集哪些内容？\n"
            "**A：** 可以记录德智体美劳五个维度的过程性表现。\n\n"
            "### Q4：能举例说明系统的减负效果吗？\n"
            "**A：** 教师可以用语音快速记录学生表现，减少重复录入。\n\n"
            "### Q5：系统是用来考核学生的吗？\n"
            "**A：** 不是，系统用于记录学生成长，不做简单考核和排名。",
            encoding="utf-8",
        )

        teacher_dir = root / "教师发展评价系统常见场景QA"
        teacher_dir.mkdir()
        (teacher_dir / "教师发展评价系统常见场景QA.md").write_text(
            "# 常见问题\n"
            "【教师】Q：系统是不是用来考核老师的？\n"
            "A：系统强调发展性评价，不是简单考核和排名。\n"
            "【教师】Q：系统能生成教师个人成长报告吗？\n"
            "A：可以生成教师个人成长报告。\n",
            encoding="utf-8",
        )
        self.knowledge_base = KnowledgeBase(root)

    def tearDown(self):
        self.temp_dir.cleanup()
        self.semantic_disabled.stop()

    def test_parses_both_qa_document_formats(self):
        self.assertEqual(len(self.knowledge_base.qa_entries), 7)

    def test_related_question_uses_standard_qa_answer(self):
        result = self.knowledge_base.search("家长能不能看孩子的成长档案？")

        self.assertTrue(result.matches)
        match = result.matches[0]
        self.assertEqual(match.kind, "qa")
        self.assertEqual(match.standard_question, "系统能否进行家校互动？")
        self.assertEqual(
            match.standard_answer,
            "可以。家长可以查看成长档案，实现家校协同。",
        )

    def test_qa_match_is_preferred_over_regular_document_retrieval(self):
        result = self.knowledge_base.search("评价系统是不是专门考核老师？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertIn("不是简单考核和排名", result.matches[0].standard_answer)

    def test_semantic_match_handles_a_paraphrase_without_shared_terms(self):
        with patch.object(
            self.knowledge_base.semantic_qa_index,
            "scores",
            return_value=[0.72, 0.21, 0.18, 0.16, 0.12, 0.18, 0.10],
        ):
            result = self.knowledge_base.search("亲子协同这块做得怎么样？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(result.matches[0].standard_question, "系统能否进行家校互动？")
        self.assertAlmostEqual(result.matches[0].semantic_score, 0.72)

    def test_student_evaluation_scope_excludes_teacher_assessment_qa(self):
        result = self.knowledge_base.search("系统是用来考核学生的吗？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(result.matches[0].standard_question, "系统是用来考核学生的吗？")
        self.assertIn("AI素养评价产品QA", result.matches[0].title)

    def test_student_recording_workload_stays_in_ai_literacy_scope(self):
        result = self.knowledge_base.search(
            "老师每天记录那么多学生表现会不会反而增加负担"
        )

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(
            result.matches[0].standard_question,
            "能举例说明系统的减负效果吗？",
        )

    def test_exam_score_paraphrase_selects_five_dimension_template(self):
        result = self.knowledge_base.search("除了考试成绩还能记录哪些方面的表现？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(
            result.matches[0].standard_question,
            "五个维度能采集哪些内容？",
        )

    def test_teacher_growth_report_selects_report_template(self):
        result = self.knowledge_base.search("老师能看到自己的成长报告吗？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(
            result.matches[0].standard_question,
            "系统能生成教师个人成长报告吗？",
        )

    def test_broader_product_cues_route_to_the_expected_qa_scope(self):
        ai_literacy_queries = (
            "小学能用吗",
            "学校先试几个班怎么开始",
            "拍照上传奖状能形成成长记录吗",
            "已有德育系统还能对接吗",
            "怎样避免评价凭印象",
        )
        teacher_queries = (
            "怎么建设骨干教师梯队",
            "教师画像能看什么",
            "一核三库五应用是什么意思",
        )
        admissions_queries = (
            "开放日可以登记家长意向吗",
            "能提供职业规划吗",
            "一体机能做校园导览吗",
        )

        for query in ai_literacy_queries:
            self.assertEqual(_priority_qa_scopes(query), {_AI_LITERACY_QA_SCOPE})
        for query in teacher_queries:
            self.assertEqual(
                _priority_qa_scopes(query),
                {_TEACHER_DEVELOPMENT_QA_SCOPE},
            )
        for query in admissions_queries:
            self.assertEqual(
                _priority_qa_scopes(query),
                {_ADMISSIONS_EMPLOYMENT_QA_SCOPE},
            )

    def test_high_confidence_paraphrases_route_directly_to_templates(self):
        cases = (
            (
                "评价会不会全凭老师印象",
                (_AI_LITERACY_QA_SCOPE, "如何保证评价的客观公正"),
            ),
            (
                "学校先拿两个班试试看",
                (_AI_LITERACY_QA_SCOPE, "从部署到上线需要多长时间"),
            ),
            (
                "能接学校的人事和教务平台吗",
                (_TEACHER_DEVELOPMENT_QA_SCOPE, "能否和学校已有系统对接"),
            ),
            (
                "招生就业平台主要解决校方哪些难题",
                (_ADMISSIONS_EMPLOYMENT_QA_SCOPE, "这套系统主要解决学校什么问题"),
            ),
            (
                "校园开放日人挤人时能分担接待吗",
                (_ADMISSIONS_EMPLOYMENT_QA_SCOPE, "开放日现场人很多"),
            ),
        )

        for query, expected in cases:
            self.assertEqual(_priority_qa_intent(query), expected, query)

    def test_ambiguous_semantic_only_match_is_rejected(self):
        with (
            patch.dict(os.environ, {"KB_QA_SEMANTIC_ENABLED": "1"}),
            patch.object(
                self.knowledge_base.semantic_qa_index,
                "scores",
                return_value=[0.63, 0.62, 0.10, 0.09, 0.08, 0.07, 0.06],
            ),
        ):
            result = self.knowledge_base.search("亲子支持方案怎么样？")

        self.assertFalse(any(match.kind == "qa" for match in result.matches))

    def test_semantic_only_match_below_threshold_is_rejected(self):
        with (
            patch.dict(os.environ, {"KB_QA_SEMANTIC_ENABLED": "1"}),
            patch.object(
                self.knowledge_base.semantic_qa_index,
                "scores",
                return_value=[0.59, 0.20, 0.10, 0.09, 0.08, 0.07, 0.06],
            ),
        ):
            result = self.knowledge_base.search("亲子协同方案怎么样？")

        self.assertFalse(any(match.kind == "qa" for match in result.matches))

    def test_colloquial_query_expansion_reaches_a_related_template(self):
        result = self.knowledge_base.search("家长能不能看小孩的成长档案？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(result.matches[0].standard_question, "系统能否进行家校互动？")

    def test_teacher_voice_recording_student_routes_to_student_product(self):
        result = self.knowledge_base.search_with_scope_documents(
            "老师对着平板说一句学生今天主动帮同学，系统接下来怎么处理？"
        )

        self.assertEqual(result.scope, _AI_LITERACY_QA_SCOPE)
        self.assertTrue(result.documents)
        self.assertTrue(
            all(document.scope == _AI_LITERACY_QA_SCOPE for document in result.documents)
        )

    def test_teacher_evaluation_system_routes_to_teacher_product(self):
        result = self.knowledge_base.search_with_scope_documents(
            "教师评价系统需要培训多久？"
        )

        self.assertEqual(result.scope, _TEACHER_DEVELOPMENT_QA_SCOPE)

    def test_ambiguous_followup_keeps_the_session_product_scope(self):
        result = self.knowledge_base.search_with_scope_documents(
            "那它还有哪些功能？",
            preferred_scope=_TEACHER_DEVELOPMENT_QA_SCOPE,
        )

        self.assertEqual(result.scope, _TEACHER_DEVELOPMENT_QA_SCOPE)
        self.assertTrue(
            all(
                document.scope == _TEACHER_DEVELOPMENT_QA_SCOPE
                for document in result.documents
            )
        )


if __name__ == "__main__":
    unittest.main()
