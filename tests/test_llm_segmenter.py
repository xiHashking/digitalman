import unittest
from types import SimpleNamespace
from unittest.mock import patch

from knowledge_base import KnowledgeMatch, KnowledgeSearchResult
from llm import SpeechSegmenter, llm_response


class SpeechSegmenterTestCase(unittest.TestCase):
    def test_emits_first_complete_sentence_while_stream_is_open(self):
        segmenter = SpeechSegmenter()
        self.assertEqual(segmenter.feed('没问题，我给您简单介绍一下'), [])
        self.assertEqual(
            segmenter.feed('咱们的AI招生就业系统。后面还有内容'),
            ['没问题，我给您简单介绍一下咱们的AI招生就业系统。'],
        )
        self.assertEqual(segmenter.flush(), '后面还有内容')

    def test_combines_short_comma_clauses_to_reduce_tts_requests(self):
        segmenter = SpeechSegmenter()
        segments = segmenter.feed('它可以线上答疑，也可以线下展示，还能减轻老师的工作压力。')
        self.assertEqual(segments, ['它可以线上答疑，也可以线下展示，还能减轻老师的工作压力。'])

    def test_hard_limit_prevents_unbounded_first_segment_latency(self):
        segmenter = SpeechSegmenter(max_chars=20)
        segments = segmenter.feed('这是一段一直没有任何标点并且会持续很长时间的模型输出内容')
        self.assertTrue(segments)
        self.assertLessEqual(len(segments[0]), 20)

    def test_qa_template_bypasses_llm_and_removes_markdown_for_speech(self):
        avatar = SimpleNamespace(
            opt=SimpleNamespace(),
            put_msg_txt=lambda text, data: spoken.append(text),
        )
        spoken = []
        result = KnowledgeSearchResult(
            query="测试",
            matches=(
                KnowledgeMatch(
                    title="标准问答",
                    text="**可以**。\n- 第一项\n- 第二项",
                    score=100,
                    coverage=1,
                    matched_terms=("测试",),
                    kind="qa",
                    standard_question="可以吗？",
                    standard_answer="**可以**。\n- 第一项\n- 第二项",
                ),
            ),
        )

        with (
            patch("llm.search_knowledge", return_value=result),
            patch("llm._llm_client") as client_factory,
        ):
            llm_response("测试", avatar)

        client_factory.assert_not_called()
        speech = "".join(spoken)
        self.assertNotIn("*", speech)
        self.assertIn("可以", speech)
        self.assertIn("第一项", speech)
        self.assertIn("第二项", speech)


if __name__ == '__main__':
    unittest.main()
