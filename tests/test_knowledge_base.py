import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import knowledge_base as knowledge_base_module
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

    def test_company_profile_question_is_reserved_for_the_strategy_prompt(self):
        result = self.knowledge_base.search("你们是哪家公司，介绍一下公司背景")

        self.assertEqual(result.matches, ())
        self.assertEqual(result.skipped_reason, "strategy_profile")

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

    def test_scope_bundle_contains_only_the_selected_product_documents(self):
        result = self.knowledge_base.search_with_scope_documents(
            "AI招生系统能提供哪些就业指导？"
        )

        self.assertEqual(result.scope, "AI宣传招生就业系统常见QA")
        self.assertEqual(
            [document.title for document in result.documents],
            ["AI招生系统介绍"],
        )
        self.assertIn("就业指导", result.scope_context())
        self.assertNotIn("职称评审", result.scope_context())

    def test_shared_policy_match_survives_product_scope_selection(self):
        with tempfile.TemporaryDirectory() as knowledge_dir:
            root = Path(knowledge_dir)
            ai_dir = root / "AI素养评价产品QA"
            policy_dir = root / "教育发展十五五规划"
            ai_dir.mkdir()
            policy_dir.mkdir()
            (ai_dir / "产品.md").write_text(
                "AI素养评价用于记录学生成长。",
                encoding="utf-8",
            )
            (policy_dir / "政策.md").write_text(
                "十五五规划提出健全德智体美劳全面培养体系。",
                encoding="utf-8",
            )
            knowledge_base = KnowledgeBase(root)

            result = knowledge_base.search_with_scope_documents(
                "十五五规划对教育评价有什么要求？"
            )

        self.assertEqual(result.scope, "AI素养评价产品QA")
        self.assertTrue(
            any(match.title == "教育发展十五五规划/政策" for match in result.matches)
        )
        self.assertTrue(
            any(document.title == "教育发展十五五规划/政策" for document in result.documents)
        )

    def test_additional_roots_merge_documents_in_the_same_scope(self):
        with tempfile.TemporaryDirectory() as additional_dir:
            additional_root = Path(additional_dir)
            qa_dir = additional_root / "AI素养评价产品QA"
            qa_dir.mkdir()
            (qa_dir / "操作说明.md").write_text(
                "# 操作说明\n\n教师可以语音记录学生的具体行为。",
                encoding="utf-8",
            )

            knowledge_base = KnowledgeBase(
                self.knowledge_base.root,
                additional_roots=(additional_root,),
            )
            result = knowledge_base.search_with_scope_documents(
                "老师怎么用语音评价学生？"
            )

        self.assertEqual(result.scope, "AI素养评价产品QA")
        self.assertIn(
            "AI素养评价产品QA/操作说明",
            [document.title for document in result.documents],
        )

    def test_duplicate_relative_documents_are_loaded_once(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_root = Path(first_dir)
            second_root = Path(second_dir)
            for root in (first_root, second_root):
                qa_dir = root / "AI素养评价产品QA"
                qa_dir.mkdir()
                (qa_dir / "相同问答.md").write_text(
                    "### Q1：怎么登录？\n**A：** 使用验证码登录。",
                    encoding="utf-8",
                )

            knowledge_base = KnowledgeBase(
                first_root,
                additional_roots=(second_root,),
            )

        matching_documents = [
            document
            for document in knowledge_base.documents
            if document.title == "AI素养评价产品QA/相同问答"
        ]
        self.assertEqual(len(matching_documents), 1)
        self.assertEqual(len(knowledge_base.qa_entries), 1)

    def test_aisales_worktree_loads_without_external_directory(self):
        previous = knowledge_base_module._kb
        try:
            knowledge_base_module._kb = None
            with patch.dict("os.environ", {}, clear=True):
                configured = knowledge_base_module._get_configured_knowledge_base()
        finally:
            knowledge_base_module._kb = previous

        self.assertIsNotNone(configured)
        self.assertTrue(
            any(
                "AI素养评价产品QA" in document.title
                for document in configured.documents
            )
        )


if __name__ == "__main__":
    unittest.main()
