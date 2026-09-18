"""Speech to text on Dialog-RSN-1, through the OpenAI Realtime SDK.

The only thing that changes from an OpenAI session is the client's base URL and key. The SDK
opens the websocket, sets the auth header, and parses every event. Audio goes up as PCM16, and
the transcript comes back as the protocol's own transcription events.

    uv run transcribe.py                       # microphone, us-1 production
    uv run transcribe.py --file speech.wav     # a 24 kHz mono PCM16 WAV instead of the mic
    uv run transcribe.py --list-devices        # pick a microphone by name or index
    uv run transcribe.py --reply               # also show what the model said back
    uv run transcribe.py --out notes.txt       # append each finished line to a file
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import time
import wave
from datetime import datetime

import sounddevice as sd
from dotenv import load_dotenv
from openai import AsyncOpenAI

MODEL = "dialog-rsn-1"
DEFAULT_URL = "https://api.us.poly.ai/v1"

# The protocol's one sample rate. A stock client streams 24 kHz without saying so, and the
# service reads an undeclared rate as 24 kHz, so the mic is opened at that rate and no
# resampling happens anywhere.
SAMPLE_RATE = 24_000
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000

# The transcript is produced as part of the model's turn, so a reply is generated whether or not
# it is shown. Asking for the shortest possible one keeps the turn quick and cheap.
INSTRUCTIONS = (
    "You are a silent transcription service. Whatever the caller says, reply with exactly "
    "one word: ok."
)

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


def b64(chunk: bytes) -> str:
    return base64.b64encode(chunk).decode("ascii")


async def pump_microphone(conn, device, stop: asyncio.Event) -> None:
    """Capture the microphone and append it to the input buffer, one chunk at a time."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_audio(indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_FRAMES,
        device=device,
        callback=on_audio,
    )
    with stream:
        while not stop.is_set():
            chunk = await queue.get()
            await conn.input_audio_buffer.append(audio=b64(chunk))


async def pump_file(conn, path: str, stop: asyncio.Event) -> None:
    """Stream a WAV file at real time, as if it were being spoken now."""
    with wave.open(path, "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, SAMPLE_RATE):
            sys.exit(
                f"{path} must be mono, 16-bit, {SAMPLE_RATE} Hz. Convert with: "
                f"afconvert -f WAVE -d LEI16@{SAMPLE_RATE} -c 1 in.wav out.wav"
            )
        started = time.monotonic()
        sent = 0
        while not stop.is_set():
            chunk = wav.readframes(CHUNK_FRAMES)
            if not chunk:
                break
            await conn.input_audio_buffer.append(audio=b64(chunk))
            sent += len(chunk) // 2
            # Hold to the file's own pace, so server-side VAD sees natural gaps.
            await asyncio.sleep(max(0.0, started + sent / SAMPLE_RATE - time.monotonic()))
    # A trailing second of silence lets the VAD close the last turn.
    for _ in range(10):
        await conn.input_audio_buffer.append(audio=b64(bytes(CHUNK_FRAMES * 2)))
        await asyncio.sleep(CHUNK_MS / 1000)


async def print_transcript(conn, show_reply: bool, out_path: str | None, stop: asyncio.Event):
    """Render transcription events as lines, one per turn."""
    out = open(out_path, "a", encoding="utf-8") if out_path else None
    partial = ""
    async for event in conn:
        kind = event.type
        if kind == "session.updated":
            print(f"[connected to {MODEL}]  speak when ready, Ctrl-C to quit\n")
        elif kind == "input_audio_buffer.speech_started":
            print("…", end="", flush=True)
        elif kind == "conversation.item.input_audio_transcription.delta":
            if not partial:
                print("\r\033[K", end="")
            partial += event.delta
            print(event.delta, end="", flush=True)
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = (event.transcript or partial).strip()
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"\r\033[K{stamp}  {text}")
            if out:
                out.write(f"{stamp}  {text}\n")
                out.flush()
            partial = ""
        elif kind == "response.output_text.done" and show_reply:
            print(f"          ↳ {event.text.strip()}")
        elif kind == "error":
            print(f"[error] {event.error.message}", file=sys.stderr)
            if event.error.type == "invalid_request_error":
                stop.set()
                break
    if out:
        out.close()


def pick_device(name: str | None):
    """A device index, or the first input device whose name contains the text."""
    if name is None:
        return None
    if name.isdigit():
        return int(name)
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            return index
    sys.exit(f"no input device matching {name!r}; try --list-devices")


async def main(args) -> None:
    load_dotenv()
    key = os.environ.get("DIALOGUE_API_KEY")
    if not key:
        sys.exit("DIALOGUE_API_KEY is not set. Copy ../.env.example to ../.env and fill it in.")
    url = args.url or os.environ.get("DIALOGUE_API_URL") or DEFAULT_URL

    client = AsyncOpenAI(api_key=key, base_url=url)
    stop = asyncio.Event()
    print(f"connecting to {url}/realtime …")
    async with client.realtime.connect(model=MODEL) as conn:
        await conn.session.update(session=SESSION)
        printer = asyncio.create_task(print_transcript(conn, args.reply, args.out, stop))
        if args.file:
            await pump_file(conn, args.file, stop)
            # Give the last turn time to come back before closing.
            await asyncio.wait({printer}, timeout=15)
        else:
            pump = asyncio.create_task(pump_microphone(conn, pick_device(args.device), stop))
            await asyncio.wait({printer, pump}, return_when=asyncio.FIRST_COMPLETED)
            pump.cancel()
        printer.cancel()


def parse_args():
    parser = argparse.ArgumentParser(description="Speech to text on Dialog-RSN-1.")
    parser.add_argument("--url", help=f"base URL, /realtime is appended (default {DEFAULT_URL})")
    parser.add_argument("--file", help="stream a 24 kHz mono PCM16 WAV instead of the microphone")
    parser.add_argument("--device", help="microphone index or name substring")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    parser.add_argument("--reply", action="store_true", help="also print the model's reply")
    parser.add_argument("--out", help="append each finished line to this file")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.list_devices:
        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                print(f"{index:3d}  {dev['name']}")
        sys.exit(0)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nbye")
