"""Speech to text on Dialog-RSN-1, through the OpenAI Realtime SDK.

The program opens a transcription session: Dialog-RSN-1 transcribes each turn and never
replies. The SDK opens the websocket, sets the auth header, and parses every event.

    uv run transcribe.py                       # the microphone, in English
    uv run transcribe.py --language es         # the caller's language, as an ISO 639 code
    uv run transcribe.py --file speech.wav     # a 24 kHz mono PCM16 WAV instead of the mic
    uv run transcribe.py --list-devices        # pick a microphone by name or index
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
DEFAULT_URL = "wss://api.studio.poly.ai/v1/realtime"

# The session declares 24 kHz and the mic opens at it, so nothing resamples on the client.
SAMPLE_RATE = 24_000
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000



def session(language: str) -> dict:
    """A transcription session: each turn ends at a pause and is transcribed, never answered."""
    return {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                "transcription": {"language": language},
                "turn_detection": {"type": "server_vad"},
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


async def print_transcript(conn, out_path: str | None, stop: asyncio.Event):
    """Render transcription events as lines, one per turn."""
    out = open(out_path, "a", encoding="utf-8") if out_path else None
    partial = ""

    def line(text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"\r\033[K{stamp}  {text.strip()}")
        if out:
            out.write(f"{stamp}  {text.strip()}\n")
            out.flush()

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
            line(event.transcript)
            partial = ""
        elif kind == "conversation.item.input_audio_transcription.failed":
            print(f"\r\033[K[not transcribed] {event.error.message}", file=sys.stderr)
            partial = ""
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
    key = os.environ.get("POLY_API_KEY")
    if not key:
        sys.exit("POLY_API_KEY is not set. Copy ../.env.example to ../.env and fill it in.")
    url = args.url or os.environ.get("DIALOG_REALTIME_URL") or DEFAULT_URL

    # The SDK takes the base URL and appends /realtime itself.
    base_url = url.removesuffix("/realtime").replace("wss://", "https://", 1)
    client = AsyncOpenAI(api_key=key, base_url=base_url)
    stop = asyncio.Event()
    print(f"connecting to {url} …")
    async with client.realtime.connect(
        model=MODEL, extra_query={"intent": "transcription"}
    ) as conn:
        await conn.session.update(session=session(args.language))
        printer = asyncio.create_task(print_transcript(conn, args.out, stop))
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
    parser.add_argument("--url", help=f"realtime URL (default {DEFAULT_URL})")
    parser.add_argument("--file", help="stream a 24 kHz mono PCM16 WAV instead of the microphone")
    parser.add_argument("--device", help="microphone index or name substring")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    parser.add_argument(
        "--language", default="en", help="the caller's language, an ISO 639 code (default en)"
    )
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
