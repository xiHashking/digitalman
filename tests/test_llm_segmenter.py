import unittest

from llm import SpeechSegmenter


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


if __name__ == '__main__':
    unittest.main()
