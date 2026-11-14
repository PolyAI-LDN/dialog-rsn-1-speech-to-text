import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch, mock_open
sys.modules.setdefault('sounddevice',SimpleNamespace())
sys.modules.setdefault('openai',SimpleNamespace(AsyncOpenAI=object))
sys.modules.setdefault('dotenv',SimpleNamespace(load_dotenv=lambda *a,**kw:None))
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from transcribe import print_transcript

class TranscriptCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_output_is_closed_on_task_cancellation(self):
        waiting=asyncio.Event()
        class Connection:
            def __aiter__(self):
                async def events():
                    waiting.set()
                    await asyncio.Event().wait()
                    yield
                return events()
        opened=mock_open()
        with patch('builtins.open',opened):
            task=asyncio.create_task(print_transcript(Connection(),False,'notes.txt',asyncio.Event()))
            await waiting.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        opened().close.assert_called_once()
