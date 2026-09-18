"""A voice-in, text-out support agent on Dialog-RSN-1, through the OpenAI Realtime SDK.

Speak, and Sam from Northwind Outfitters answers in text. Everything Sam knows is in
`prompt.py`: three orders and the return policy, all in the instructions, no tools.

    uv run support_agent.py                     # microphone, us-1 production
    uv run support_agent.py --file call.wav     # a 24 kHz mono PCM16 WAV instead of the mic
    uv run support_agent.py --list-devices      # pick a microphone by name or index
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from openai import AsyncOpenAI

from prompt import GREETING, INSTRUCTIONS
from transcribe import DEFAULT_URL, MODEL, SAMPLE_RATE, pick_device, pump_file, pump_microphone

SESSION = {
    "type": "realtime",
    "output_modalities": ["text"],
    "instructions": INSTRUCTIONS,
    "audio": {
        "input": {
            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
            "turn_detection": {"type": "server_vad", "create_response": True},
        }
    },
}


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def print_conversation(conn, stop: asyncio.Event) -> None:
    """Render the call: what you said, then Sam's reply.

    The model emits its reply before the transcript of what it heard, since the transcript is
    the last section of its output. The reply is held back until the transcript has printed,
    so the terminal reads in the order the conversation happened.
    """
    heard = ""
    reply = ""
    reply_shown = False
    async for event in conn:
        kind = event.type
        if kind == "session.updated":
            print(f"[connected to {MODEL}]  speak when ready, Ctrl-C to quit\n")
            print(f"{stamp()}  Sam:  {GREETING}\n")
        elif kind == "input_audio_buffer.speech_started":
            print("…", end="", flush=True)
        elif kind == "conversation.item.input_audio_transcription.delta":
            heard += event.delta
        elif kind == "conversation.item.input_audio_transcription.completed":
            print(f"\r\033[K{stamp()}  You:  {(event.transcript or heard).strip()}")
            heard = ""
            if reply:
                print(f"{stamp()}  Sam:  {reply}", end="", flush=True)
                reply_shown = True
        elif kind == "response.output_text.delta":
            reply += event.delta
            if reply_shown:
                print(event.delta, end="", flush=True)
        elif kind == "response.output_text.done":
            if not reply_shown:
                print(f"\r\033[K{stamp()}  Sam:  {event.text}", end="")
            print("\n")
            reply = ""
            reply_shown = False
        elif kind == "error":
            print(f"[error] {event.error.message}", file=sys.stderr)
            if event.error.type == "invalid_request_error":
                stop.set()
                break


async def main(args) -> None:
    load_dotenv()
    key = os.environ.get("DIALOGUE_API_KEY")
    if not key:
        sys.exit("DIALOGUE_API_KEY is not set. Copy .env.example to .env and fill it in.")
    url = args.url or os.environ.get("DIALOGUE_API_URL") or DEFAULT_URL

    client = AsyncOpenAI(api_key=key, base_url=url)
    stop = asyncio.Event()
    print(f"connecting to {url}/realtime …")
    async with client.realtime.connect(model=MODEL) as conn:
        await conn.session.update(session=SESSION)
        # The greeting is printed locally rather than generated, so the model's history starts
        # with it too: an assistant message the model can refer back to.
        await conn.conversation.item.create(
            item={
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": GREETING}],
            }
        )
        printer = asyncio.create_task(print_conversation(conn, stop))
        if args.file:
            await pump_file(conn, args.file, stop)
            await asyncio.wait({printer}, timeout=20)
        else:
            pump = asyncio.create_task(pump_microphone(conn, pick_device(args.device), stop))
            await asyncio.wait({printer, pump}, return_when=asyncio.FIRST_COMPLETED)
            pump.cancel()
        printer.cancel()


def parse_args():
    parser = argparse.ArgumentParser(description="Voice-in, text-out support agent.")
    parser.add_argument("--url", help=f"base URL, /realtime is appended (default {DEFAULT_URL})")
    parser.add_argument("--file", help="stream a 24 kHz mono PCM16 WAV instead of the microphone")
    parser.add_argument("--device", help="microphone index or name substring")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.list_devices:
        import sounddevice as sd

        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                print(f"{index:3d}  {dev['name']}")
        sys.exit(0)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nbye")
