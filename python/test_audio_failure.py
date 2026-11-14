import asyncio
import importlib
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as Event
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
sys.modules.setdefault('sounddevice',Event())
sys.modules.setdefault('openai',Event(AsyncOpenAI=object))
sys.modules.setdefault('dotenv',Event(load_dotenv=lambda *a,**kw:None))

class AudioFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_errors_propagate_and_the_other_worker_is_closed(self):
        for name,printer_name in [('transcribe','print_transcript'),('support_agent','print_conversation')]:
            for file_mode in (False,True):
                for failing in ('audio','reader'):
                    with self.subTest(client=name,file=file_mode,failing=failing):
                        module=importlib.import_module(name)
                        closed=asyncio.Event()
                        async def worker(which):
                            if which==failing:raise RuntimeError(which+' unavailable')
                            try:await asyncio.Event().wait()
                            finally:closed.set()
                        async def reader(*args):await worker('reader')
                        async def audio(*args):await worker('audio')
                        async def noop(**kwargs):pass
                        class Connection:
                            session=Event(update=noop)
                            conversation=Event(item=Event(create=noop))
                            async def __aenter__(self):return self
                            async def __aexit__(self,*args):pass
                        client=Event(realtime=Event(connect=lambda **kwargs:Connection()))
                        args=Event(url=None,file='call.wav' if file_mode else None,device=None,reply=False,out=None)
                        with patch.dict('os.environ',{'POLY_API_KEY':'offline-test'}),patch.object(module,'AsyncOpenAI',return_value=client),patch.object(module,printer_name,reader),patch.object(module,'pump_microphone',audio),patch.object(module,'pump_file',audio),patch.object(module,'pick_device',return_value=None):
                            with self.assertRaisesRegex(RuntimeError,failing+' unavailable'):
                                await asyncio.wait_for(module.main(args),timeout=0.2)
                        self.assertTrue(closed.is_set())
