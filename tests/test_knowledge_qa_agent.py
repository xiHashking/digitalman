import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from knowledge_base import KnowledgeDocument, KnowledgeSearchResult
from knowledge_qa_agent import KnowledgeQaError, answer_knowledge_question


class KnowledgeQaAgentTestCase(unittest.TestCase):
    def setUp(self):
        self.result = KnowledgeSearchResult(
            query="家长能看到什么？",
            matches=(),
            scope="AI素养评价产品QA",
            documents=(
                KnowledgeDocument(
                    scope="AI素养评价产品QA",
                    title="产品介绍/产品介绍",
                    text="家长可以查看孩子的成长档案，了解过程性表现。",
                ),
            ),
        )

    @staticmethod
    def _client_with_payloads(*payloads):
        responses = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(payload, ensure_ascii=False)
                        )
                    )
                ]
            )
            for payload in payloads
        ]
        create = Mock(side_effect=responses)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    def test_accepts_answer_with_verbatim_evidence(self):
        client = self._client_with_payloads(
            {
                "answer": "家长可以查看孩子的成长档案。",
                "evidence": [
                    {
                        "source": "产品介绍/产品介绍.md",
                        "quote": "家长可以查看孩子的成长档案",
                    }
                ],
                "gaps": [],
            }
        )

        with patch.dict("os.environ", {"LLM_DISABLE_REASONING": "1"}):
            answer = answer_knowledge_question(
                client,
                model="test-model",
                question=self.result.query,
                result=self.result,
            )

        self.assertEqual(answer.answer, "家长可以查看孩子的成长档案。")
        self.assertEqual(answer.evidence[0].source, "产品介绍/产品介绍")
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["temperature"], 0.1)
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})

    def test_retries_when_quote_is_not_in_source_document(self):
        client = self._client_with_payloads(
            {
                "answer": "系统保证家长满意。",
                "evidence": [
                    {"source": "产品介绍/产品介绍", "quote": "保证家长满意"}
                ],
                "gaps": [],
            },
            {
                "answer": "家长可以查看孩子的成长档案。",
                "evidence": [
                    {
                        "source": "产品介绍/产品介绍",
                        "quote": "家长可以查看孩子的成长档案",
                    }
                ],
                "gaps": [],
            },
        )

        answer = answer_knowledge_question(
            client,
            model="test-model",
            question=self.result.query,
            result=self.result,
        )

        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertNotIn("保证", answer.answer)

    def test_rejects_two_unverifiable_factual_answers(self):
        invalid = {
            "answer": "系统保证家长满意。",
            "evidence": [
                {"source": "产品介绍/产品介绍", "quote": "保证家长满意"}
            ],
            "gaps": [],
        }
        client = self._client_with_payloads(invalid, invalid)

        with self.assertRaises(KnowledgeQaError):
            answer_knowledge_question(
                client,
                model="test-model",
                question=self.result.query,
                result=self.result,
            )

    def test_allows_explicit_information_gap_without_evidence(self):
        client = self._client_with_payloads(
            {
                "answer": "现有资料没有明确说明，需要进一步确认。",
                "evidence": [],
                "gaps": ["服务学校数量"],
            }
        )

        answer = answer_knowledge_question(
            client,
            model="test-model",
            question="服务了多少学校？",
            result=self.result,
        )

        self.assertFalse(answer.evidence)
        self.assertEqual(answer.gaps, ("服务学校数量",))

    def test_gap_cannot_mask_an_unsupported_factual_answer(self):
        invalid = {
            "answer": "系统已经服务了一千所学校。",
            "evidence": [],
            "gaps": ["缺少服务学校清单"],
        }
        client = self._client_with_payloads(invalid, invalid)

        with self.assertRaises(KnowledgeQaError):
            answer_knowledge_question(
                client,
                model="test-model",
                question="服务了多少学校？",
                result=self.result,
            )

    def test_strategy_document_can_ground_company_answer_without_product_match(self):
        result = KnowledgeSearchResult(query="你们是哪家公司？", matches=())
        strategy_document = KnowledgeDocument(
            scope="数字员工策略",
            title="数字员工策略/main.md",
            text="你是四川乐途智学的曹老师，负责教育产品咨询。",
        )
        client = self._client_with_payloads(
            {
                "answer": "我是四川乐途智学的曹老师，负责教育产品咨询。",
                "evidence": [
                    {
                        "source": "数字员工策略/main.md",
                        "quote": "四川乐途智学的曹老师",
                    }
                ],
                "gaps": [],
            }
        )

        answer = answer_knowledge_question(
            client,
            model="test-model",
            question=result.query,
            result=result,
            strategy_documents=(strategy_document,),
        )

        self.assertIn("四川乐途智学", answer.answer)
        request = client.chat.completions.create.call_args.kwargs
        system_prompt = request["messages"][0]["content"]
        self.assertIn("每次只处理当前用户问题", system_prompt)
        self.assertIn("数字员工策略/main.md", system_prompt)


if __name__ == "__main__":
    unittest.main()
