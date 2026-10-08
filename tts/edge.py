import time
import asyncio
import os
import numpy as np
import resampy
import soundfile as sf
import edge_tts
from io import BytesIO

from utils.logger import logger
from .base_tts import BaseTTS, State
from registry import register

@register("tts", "edgetts")
class EdgeTTS(BaseTTS):
    def txt_to_audio(self,msg:tuple[str, dict]):
        text,textevent = msg
        voice = self.opt.REF_FILE or "zh-CN-YunxiaNeural" 
        voicename = textevent.get('tts', {}).get('ref_file',voice) #self.opt.REF_FILE #"zh-CN-YunxiaNeural"
        t = time.time()
        try:
            max_attempts = max(1, int(os.getenv('EDGE_TTS_MAX_ATTEMPTS', '2')))
        except ValueError:
            max_attempts = 2

        success = False
        for attempt in range(1, max_attempts + 1):
            self.input_stream.seek(0)
            self.input_stream.truncate()
            success = asyncio.run(self.__main(voicename, text))
            if success and self.input_stream.getbuffer().nbytes > 0:
                break
            if self.state != State.RUNNING:
                return
            if attempt < max_attempts:
                logger.warning('[TTS] Edge attempt %d/%d failed; retrying', attempt, max_attempts)
                time.sleep(0.2 * attempt)

        logger.info(
            '-------edge tts time:%.4fs attempts=%d',
            time.time() - t,
            attempt,
        )
        if not success or self.input_stream.getbuffer().nbytes <= 0:
            raise RuntimeError(f'Edge TTS failed after {max_attempts} attempts')
        
        self.input_stream.seek(0)
        stream = self.__create_bytes_stream(self.input_stream)
        streamlen = stream.shape[0]
        idx=0
        while streamlen >= self.chunk and self.state==State.RUNNING:
            eventpoint={}
            streamlen -= self.chunk
            if idx==0:
                eventpoint={'status':'start','text':text}
            elif streamlen<self.chunk:
                eventpoint={'status':'end','text':text}
            eventpoint.update(**textevent) #eventpoint={'status':'end','text':text,'msgevent':textevent}
            self.parent.put_audio_frame(stream[idx:idx+self.chunk],eventpoint)
            idx += self.chunk
        #if streamlen>0:  #skip last frame(not 20ms)
        #    self.queue.put(stream[idx:])
        self.input_stream.seek(0)
        self.input_stream.truncate() 

    def __create_bytes_stream(self,byte_stream):
        #byte_stream=BytesIO(buffer)
        stream, sample_rate = sf.read(byte_stream) # [T*sample_rate,] float64
        logger.info(f'[INFO]tts audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]
    
        if sample_rate != self.sample_rate and stream.shape[0]>0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream
    
    async def __main(self,voicename: str, text: str) -> bool:
        try:
            communicate = edge_tts.Communicate(
                text,
                voicename,
                connect_timeout=2,
                receive_timeout=15,
            )

            received_audio = False
            async for chunk in communicate.stream():
                if chunk["type"] == "audio" and self.state==State.RUNNING:
                    self.input_stream.write(chunk["data"])
                    received_audio = True
                elif chunk["type"] == "WordBoundary":
                    pass
            return received_audio
        except Exception as e:
            logger.warning('[TTS] Edge connection failed: %s', e)
            return False
