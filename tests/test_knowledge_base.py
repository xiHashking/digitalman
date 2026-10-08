import tempfile
import unittest
from pathlib import Path

from knowledge_base import KnowledgeBase


class KnowledgeBaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "AI招生系统介绍.md").write_text(
            "# AI宣传招生就业系统\n\n"
            "系统支持招生政策问答、校园导览和就业指导，"
            "可以部署在线上AI助理和线下一体机。AI招生系统培训通常需要一周。",
            encoding="utf-8",
        )
        (root / "教师发展评价.md").write_text(
            "# 教师发展评价\n\n"
            "系统建立教师发展档案，支持职称评审材料归档和多维度评价。"
            "教师评价系统培训通常需要三天。",
            encoding="utf-8",
        )
        self.knowledge_base = KnowledgeBase(root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_skips_greeting_instead_of_matching_common_characters(self):
        result = self.knowledge_base.search("你好")

        self.assertEqual(result.matches, ())
        self.assertEqual(result.skipped_reason, "casual_conversation")

    def test_skips_feedback_about_the_knowledge_base(self):
        result = self.knowledge_base.search("这个知识库肯定有问题")

        self.assertEqual(result.matches, ())
        self.assertEqual(result.skipped_reason, "knowledge_base_feedback")

    def test_matches_a_domain_question_with_source_and_evidence(self):
        result = self.knowledge_base.search("AI招生系统能提供哪些就业指导？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].title, "AI招生系统介绍")
        self.assertIn("就业指导", result.matches[0].text)
        self.assertIn("来源：AI招生系统介绍", result.context())

    def test_does_not_match_an_unrelated_question(self):
        result = self.knowledge_base.search("今天天气怎么样")

        self.assertEqual(result.matches, ())

    def test_explicit_product_terms_limit_results_to_that_scope(self):
        result = self.knowledge_base.search("教师评价系统需要培训多久？")

        self.assertTrue(result.matches)
        self.assertEqual(result.matches[0].title, "教师发展评价")

    def test_skips_a_question_that_is_ambiguous_across_products(self):
        result = self.knowledge_base.search("系统培训通常需要多久？")

        self.assertEqual(result.matches, ())
        self.assertEqual(result.skipped_reason, "ambiguous_scope")


if __name__ == "__main__":
    unittest.main()
