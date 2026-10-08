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
            "**A：** 通常在正式部署后一周内即可熟练掌握。",
            encoding="utf-8",
        )

        teacher_dir = root / "教师发展评价系统常见场景QA"
        teacher_dir.mkdir()
        (teacher_dir / "教师发展评价系统常见场景QA.md").write_text(
            "# 常见问题\n"
            "【教师】Q：系统是不是用来考核老师的？\n"
            "A：系统强调发展性评价，不是简单考核和排名。\n",
            encoding="utf-8",
        )
        self.knowledge_base = KnowledgeBase(root)

    def tearDown(self):
        self.temp_dir.cleanup()
        self.semantic_disabled.stop()

    def test_parses_both_qa_document_formats(self):
        self.assertEqual(len(self.knowledge_base.qa_entries), 3)

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
            return_value=[0.72, 0.21, 0.18],
        ):
            result = self.knowledge_base.search("亲子协同这块做得怎么样？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(result.matches[0].standard_question, "系统能否进行家校互动？")
        self.assertAlmostEqual(result.matches[0].semantic_score, 0.72)

    def test_colloquial_query_expansion_reaches_a_related_template(self):
        result = self.knowledge_base.search("家长能不能看小孩的成长档案？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].kind, "qa")
        self.assertEqual(result.matches[0].standard_question, "系统能否进行家校互动？")


if __name__ == "__main__":
    unittest.main()
