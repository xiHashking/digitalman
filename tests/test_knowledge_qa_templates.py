import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from knowledge_base import KnowledgeBase


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


if __name__ == "__main__":
    unittest.main()
