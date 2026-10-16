import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aisales_strategy import load_ai_sales_strategy


class AiSalesStrategyTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.temp_dir.name)
        strategy = self.worktree / "ai_sales_education"
        skills = strategy / "skills"
        products = self.worktree / "products" / "ai_sales_education"
        skills.mkdir(parents=True)
        products.mkdir(parents=True)
        (products / "产品资料.md").write_text("产品事实。", encoding="utf-8")
        (strategy / "main.md").write_text(
            "# 曹老师\n你是四川乐途智学的曹老师。\n\n"
            "## 核心人设\n表达专业克制。\n\n"
            "## 对话阶段判断\n个人版目前免费。\n\n"
            "## CRM内部流程\n这段不应加载。\n\n"
            "## 输出原则\n先给结论。\n\n"
            "## 初始化\n使用固定公司口径。\n",
            encoding="utf-8",
        )
        (skills / "basic.md").write_text(
            "公司基础介绍。",
            encoding="utf-8",
        )
        (skills / "case.md").write_text(
            "匿名案例口径。",
            encoding="utf-8",
        )
        (strategy / "prompt_assembly.yaml").write_text(
            "name: ai_sales_education\n"
            "main_agent_prompt:\n"
            "  cached:\n"
            "    - path: main.md\n"
            "system_skills:\n"
            "  - name: basic-product-information-introduction\n"
            "    path: skills/basic.md\n"
            "  - name: case-introduction\n"
            "    path: skills/case.md\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _load(self, question: str):
        with patch.dict(
            "os.environ",
            {
                "AISALES_PROMPT_WORKTREE": str(self.worktree),
                "AISALES_STRATEGY_NAME": "ai_sales_education",
                "AISALES_PRODUCT_NAME": "ai_sales_education",
            },
            clear=False,
        ):
            return load_ai_sales_strategy(question)

    def test_loads_core_persona_and_basic_skill(self):
        bundle = self._load("你们公司是做什么的？")

        self.assertIsNotNone(bundle)
        context = bundle.context()
        self.assertIn("四川乐途智学", context)
        self.assertIn("公司基础介绍", context)
        self.assertNotIn("CRM内部流程", context)
        self.assertNotIn("匿名案例口径", context)

    def test_loads_case_skill_only_for_case_question(self):
        bundle = self._load("你们有学校落地案例吗？")

        self.assertIn("匿名案例口径", bundle.context())

    def test_loads_transfer_skill_for_spoken_price_question(self):
        strategy = self.worktree / "ai_sales_education"
        transfer = strategy / "skills" / "transfer.md"
        transfer.write_text("价格必须按策略回答。", encoding="utf-8")
        assembly = strategy / "prompt_assembly.yaml"
        assembly.write_text(
            assembly.read_text(encoding="utf-8")
            + "  - name: transfer-to-human-rule\n"
            + "    path: skills/transfer.md\n",
            encoding="utf-8",
        )

        bundle = self._load("4000名学生一年多少钱？")

        self.assertIn("价格必须按策略回答", bundle.context())

    def test_loads_bc_split_section_only_for_personal_or_school_usage(self):
        personal = self._load("个人老师体验版收费吗？")
        generic = self._load("你们公司是做什么的？")

        self.assertIn("个人版目前免费", personal.context())
        self.assertNotIn("个人版目前免费", generic.context())

    def test_uses_product_directory_from_the_same_worktree(self):
        bundle = self._load("系统有什么作用？")

        self.assertEqual(
            bundle.product_root,
            (self.worktree / "products" / "ai_sales_education").resolve(),
        )

    def test_uses_project_local_strategy_when_no_external_worktree_is_configured(self):
        with patch.dict(
            "os.environ",
            {"AISALES_PROMPT_WORKTREE": ""},
            clear=False,
        ):
            bundle = load_ai_sales_strategy("你们公司是做什么的？")

        self.assertIsNotNone(bundle)
        self.assertEqual(
            bundle.strategy_root,
            (
                Path(__file__).resolve().parents[1]
                / "knowledge_sources"
                / "03_digital_employee_strategy"
            ).resolve(),
        )
        self.assertIn("四川乐途智学科技有限公司", bundle.context())
        self.assertIn("可编辑提示词/04_answer_boundaries.md", bundle.context())


if __name__ == "__main__":
    unittest.main()
