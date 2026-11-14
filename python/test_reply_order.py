import asyncio
import contextlib
import io
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as Event
sys.path.insert(0,str(Path(__file__).parent))
sys.modules.setdefault('sounddevice',Event())
sys.modules.setdefault('openai',Event(AsyncOpenAI=object))
sys.modules.setdefault('dotenv',Event(load_dotenv=lambda *a,**kw:None))
from support_agent import print_conversation

class ReplyOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_reply_finishing_before_transcription_is_printed_after_caller(self):
        events=[Event(type='response.created'),Event(type='response.output_text.delta',delta='Order shipped.'),
            Event(type='response.output_text.done',text='Order shipped.'),
            Event(type='conversation.item.input_audio_transcription.completed',transcript='Where is my order?'),
            Event(type='response.done',response=Event(status='completed'))]
        class Connection:
            def __aiter__(self):
                async def generate():
                    for event in events:yield event
                return generate()
        out=io.StringIO()
        with contextlib.redirect_stdout(out):await print_conversation(Connection(),asyncio.Event())
        rendered=out.getvalue()
        self.assertLess(rendered.index('You:'),rendered.index('Sam:'))
        self.assertEqual(rendered.count('Order shipped.'),1)
    async def test_interrupted_partial_transcription_is_not_carried_to_next_turn(self):
        events=[Event(type='response.created'),Event(type='conversation.item.input_audio_transcription.delta',delta='Old partial'),
            Event(type='response.done',response=Event(status='cancelled')),Event(type='response.created'),
            Event(type='conversation.item.input_audio_transcription.completed',transcript='New question'),
            Event(type='response.output_text.delta',delta='New answer'),
            Event(type='response.output_text.done',text='New answer'),Event(type='response.done',response=Event(status='completed'))]
        class Connection:
            def __aiter__(self):
                async def generate():
                    for event in events:yield event
                return generate()
        out=io.StringIO()
        with contextlib.redirect_stdout(out):await print_conversation(Connection(),asyncio.Event())
        self.assertIn('Old partial',out.getvalue())
        self.assertEqual(out.getvalue().count('New answer'),1)

    async def test_response_done_can_precede_transcript(self):
        events=[Event(type='response.created'),Event(type='response.output_text.delta',delta='Order shipped.'),
            Event(type='response.output_text.done',text='Order shipped.'),
            Event(type='response.done',response=Event(status='completed')),
            Event(type='conversation.item.input_audio_transcription.completed',transcript='Where is my order?')]
        class Connection:
            def __aiter__(self):
                async def generate():
                    for event in events:yield event
                return generate()
        out=io.StringIO()
        with contextlib.redirect_stdout(out):await print_conversation(Connection(),asyncio.Event())
        self.assertLess(out.getvalue().index('You:'),out.getvalue().index('Sam:'))

    async def test_transcript_can_precede_response_created(self):
        events=[Event(type='conversation.item.input_audio_transcription.completed',transcript='Where is my order?'),
            Event(type='response.created'),Event(type='response.output_text.delta',delta='Order shipped.'),
            Event(type='response.output_text.done',text='Order shipped.'),
            Event(type='response.done',response=Event(status='completed'))]
        class Connection:
            def __aiter__(self):
                async def generate():
                    for event in events:
                        yield event
                        if event.type=='response.output_text.done':
                            self.test.assertIn('Order shipped.',out.getvalue())
                self.test=self_test
                return generate()
        self_test=self
        out=io.StringIO()
        with contextlib.redirect_stdout(out):await print_conversation(Connection(),asyncio.Event())
        self.assertEqual(out.getvalue().count('Order shipped.'),1)
