# Speech to text on Dialog-RSN-1

A terminal transcriber. Your microphone streams to Dialog-RSN-1 through the OpenAI Realtime
SDK, and each finished sentence prints as a line. The only thing that differs from an OpenAI
session is the client's base URL and key.

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=DIALOGUE_API_KEY, base_url="https://api.us.poly.ai/v1")

async with client.realtime.connect(model="dialog-rsn-1") as conn:
    await conn.session.update(session={...})
    await conn.input_audio_buffer.append(audio=base64_pcm16)
    async for event in conn:
        if event.type == "conversation.item.input_audio_transcription.completed":
            print(event.transcript)
```

The SDK builds `wss://api.us.poly.ai/v1/realtime?model=dialog-rsn-1`, sends the key as
`Authorization: Bearer`, and parses every event into its typed models. Nothing is patched.

## Setup

```bash
cp .env.example .env      # then paste a workspace API key from Agent Studio
uv sync
uv run transcribe.py
```

Needs Python 3.12 and a microphone. Personal access tokens are refused by the service; use a
workspace key.

## Usage

| Flag | What it does |
| --- | --- |
| `--list-devices` | print input devices and exit |
| `--device NAME` | pick a microphone by index or name substring |
| `--file speech.wav` | stream a 24 kHz mono PCM16 WAV at real time instead of the mic |
| `--reply` | also print what the model said back |
| `--out notes.txt` | append each finished line to a file |
| `--url URL` | another region, for example `https://api.dev.poly.ai/v1` |

To make a test clip on a Mac:

```bash
say -v Samantha -o clip.aiff "Table for two on Friday, please."
afconvert -f WAVE -d LEI16@24000 -c 1 clip.aiff clip.wav
uv run transcribe.py --file clip.wav
```

## How it works

- **Audio in.** The mic opens at 24 kHz, 16-bit mono, the protocol's one sample rate. Each
  100 ms chunk is base64 encoded and sent with `input_audio_buffer.append`. No resampling.
- **Turn taking.** The session asks for `server_vad` with `create_response`, so the service
  decides when you stopped talking and starts a turn on its own.
- **Transcript out.** Dialog-RSN-1 transcribes as part of generating its turn. The words arrive
  as `conversation.item.input_audio_transcription.delta` events, then one `completed` event
  with the full sentence. The delta renders inline; the completed line replaces it with a
  timestamp.
- **The reply.** A turn always produces a text reply. The instructions ask for the single word
  "ok" so it is fast and cheap. `--reply` shows it, mainly as proof the model heard you.
- **Text only.** `output_modalities` is `["text"]`. The service has no TTS, so nothing plays.

## What this is not

No diarisation, no punctuation control, no interim results beyond the streamed deltas. It is a
demonstration that a stock OpenAI Realtime client works unchanged against Dialog-RSN-1.
