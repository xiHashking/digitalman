from threading import Thread
from threading import Lock
import html
import queue
import re
import uuid
from queue import Queue
from io import BytesIO
from enum import Enum

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar

from utils.logger import logger


def sanitize_tts_text(text: str) -> str:
    """Convert common Markdown output into natural text before synthesis."""
    if not text:
        return ""

    text = html.unescape(str(text))
    text = re.sub(r"```(?:\w+)?\s*(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"(?m)^\s*[-+*]\s+", "", text)
    text = re.sub(r"[*_~]{1,3}", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n+\s*", "，", text)
    text = re.sub(r"，{2,}", "，", text)
    return text.strip(" ，")

class State(Enum):
    RUNNING = 0
    PAUSE = 1

class BaseTTS:
    def __init__(self, opt, parent: "BaseAvatar"):
        self.opt = opt
        self.parent = parent

        #self.fps = opt.fps # 20 ms per frame
        self.sample_rate = 16000
        self.chunk = self.sample_rate // (opt.fps*2) # 320 samples per chunk (20ms * 16000 / 1000)
        self.input_stream = BytesIO()

        self.msgqueue = Queue()
        self.state = State.RUNNING
        self._busy_lock = Lock()
        self._processing = False
        self._pending_playback = set()

    def flush_talk(self):
        with self.msgqueue.mutex:
            self.msgqueue.queue.clear()
        with self._busy_lock:
            self._pending_playback.clear()
        self.state = State.PAUSE

    def put_msg_txt(self, msg: str, datainfo: dict = {}):
        clean_msg = sanitize_tts_text(msg)
        if not clean_msg:
            return

        event_info = dict(datainfo or {})
        segment_id = uuid.uuid4().hex
        event_info['_tts_segment_id'] = segment_id
        with self._busy_lock:
            self._pending_playback.add(segment_id)
        self.msgqueue.put((clean_msg, event_info))

    def mark_playback_end(self, eventpoint: dict):
        segment_id = eventpoint.get('_tts_segment_id') if eventpoint else None
        if not segment_id:
            return
        with self._busy_lock:
            self._pending_playback.discard(segment_id)

    def is_busy(self) -> bool:
        with self._busy_lock:
            pending = bool(self._pending_playback)
            processing = self._processing
        return pending or processing or not self.msgqueue.empty()

    def render(self, quit_event):
        process_thread = Thread(target=self.process_tts, args=(quit_event,))
        process_thread.start()
    
    def process_tts(self, quit_event):        
        while not quit_event.is_set():
            try:
                msg: tuple[str, dict] = self.msgqueue.get(block=True, timeout=1)
                self.state = State.RUNNING
            except queue.Empty:
                continue
            with self._busy_lock:
                self._processing = True
            try:
                self.txt_to_audio(msg)
            finally:
                with self._busy_lock:
                    self._processing = False
        self.stop_tts()
        logger.info('ttsreal thread stop')
    
    def txt_to_audio(self, msg: tuple[str, dict]):
        pass

    def stop_tts(self):
        pass
