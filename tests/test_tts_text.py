import unittest
import time
from threading import Event, Thread
from types import SimpleNamespace

from tts.base_tts import BaseTTS, sanitize_tts_text


class SanitizeTTSTextTestCase(unittest.TestCase):
    def test_removes_markdown_emphasis_without_removing_content(self):
        self.assertEqual(sanitize_tts_text('比如 **123** 和 __AI系统__'), '比如 123 和 AI系统')

    def test_converts_links_and_lists_to_spoken_text(self):
        source = '- [招生系统](https://example.com)\n- `使用说明`'
        self.assertEqual(sanitize_tts_text(source), '招生系统，使用说明')

    def test_removes_headings_and_quote_markers(self):
        self.assertEqual(sanitize_tts_text('## 标题\n> 正文'), '标题，正文')

    def test_busy_state_lasts_until_segment_playback_ends(self):
        tts = BaseTTS(SimpleNamespace(fps=25), parent=object())
        tts.put_msg_txt('**回答**')
        self.assertTrue(tts.is_busy())

        text, eventpoint = tts.msgqueue.get_nowait()
        self.assertEqual(text, '回答')
        self.assertTrue(tts.is_busy())

        tts.mark_playback_end(eventpoint)
        self.assertFalse(tts.is_busy())

    def test_failed_segment_releases_busy_state_and_worker_survives(self):
        class BrokenTTS(BaseTTS):
            def txt_to_audio(self, msg):
                raise RuntimeError('network failed')

        tts = BrokenTTS(SimpleNamespace(fps=25), parent=object())
        quit_event = Event()
        worker = Thread(target=tts.process_tts, args=(quit_event,), daemon=True)
        worker.start()
        tts.put_msg_txt('测试失败清理')

        deadline = time.time() + 1
        while tts.is_busy() and time.time() < deadline:
            time.sleep(0.01)

        self.assertFalse(tts.is_busy())
        self.assertTrue(worker.is_alive())
        quit_event.set()
        worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
