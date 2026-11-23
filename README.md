# Speech to text on Dialog-RSN-1

Companion code for [Transcribe speech with the OpenAI Realtime SDK](https://docs.poly.ai/dialog-rsn-1/guides/speech-to-text).

Small terminal programs on the OpenAI Realtime SDK, pointed at Dialog-RSN-1:

- `python/transcribe.py` and `node/transcribe.js`, a transcriber. Speak, and each finished
  sentence prints as a line.
- `python/support_agent.py` and `node/support_agent.js`, a voice-in, text-out chat bot. Speak,
  and Sam from Northwind Outfitters answers in text about three made-up orders.

## The transcriber

Your microphone streams to a Dialog-RSN-1 transcription session, which transcribes every turn
and never replies. You pay for each turn's audio and its transcript, and there are no
instructions to write. The only thing that differs from an OpenAI session is the client's base
URL and key.

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=POLY_API_KEY, base_url="https://api.studio.poly.ai/v1")

async with client.realtime.connect(
    model="dialog-rsn-1", extra_query={"intent": "transcription"}
) as conn:
    await conn.session.update(session={"type": "transcription", ...})
    await conn.input_audio_buffer.append(audio=base64_pcm16)
    async for event in conn:
        if event.type == "conversation.item.input_audio_transcription.completed":
            print(event.transcript)
```

The SDK builds `wss://api.studio.poly.ai/v1/realtime` with `model` and `intent=transcription` in
the query, sends the key as `Authorization: Bearer`, and parses every event into its typed models. Nothing
is patched.

## Setup

```bash
git clone https://github.com/PolyAI-LDN/dialog-rsn-1-speech-to-text
cd dialog-rsn-1-speech-to-text
cp .env.example .env      # add POLY_API_KEY
```

The `.env` lives at the repo root and is shared by the Python and Node programs. Get a
Dialog-RSN-1 API key from [studio.poly.ai](https://studio.poly.ai): sign up or log in, then
create one in the **API Keys** tab. To use another host, set `DIALOG_REALTIME_URL` in `.env` or
pass `--url`.

Python needs Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
cd python
uv sync
uv run transcribe.py
```

Node needs Node 22. `npm install` compiles the `naudiodon2` audio package, so you need a C++
toolchain:

```bash
cd node
npm install
npm run transcribe
```

Speak, and each line prints when you pause:

```text
connecting to wss://api.studio.poly.ai/v1/realtime …
[connected to dialog-rsn-1]  speak when ready, Ctrl-C to quit

13:24:49  Hi, I'm calling about order N W one zero four eight two. It's under Maria Alvarez.
13:24:51  Can I still cancel it?
```

## Usage

| Flag | What it does |
| --- | --- |
| `--language CODE` | the caller's language, as an ISO 639 code such as `es` or `en-US` (default `en`) |
| `--list-devices` | print input devices and exit |
| `--device NAME` | pick a microphone by index or name substring |
| `--file speech.wav` | stream a 24 kHz mono PCM16 WAV at real time instead of the mic |
| `--out notes.txt` | append each finished line to a file (Python only) |
| `--url URL` | another realtime URL (default `wss://api.studio.poly.ai/v1/realtime`) |

In Node, pass flags after `--`, as in `npm run transcribe -- --language es`.

To make a test clip on a Mac:

```bash
say -v Samantha -o clip.aiff "Table for two on Friday, please."
afconvert -f WAVE -d LEI16@24000 -c 1 clip.aiff clip.wav
uv run transcribe.py --file clip.wav
```

## How it works

- **The session.** The program connects with `intent=transcription` and sends a
  `session.update` with `type: "transcription"` and the caller's language in
  `audio.input.transcription.language`. `session.updated` echoes it back.
- **Audio in.** The mic opens at 24 kHz, 16-bit mono, the rate the session declares. Each
  100 ms chunk is base64 encoded and sent with `input_audio_buffer.append`. The client does no
  resampling.
- **Turns.** The session uses `server_vad`, so each turn ends at a pause and the server commits
  it. The program never commits the buffer. A caller who pauses longer than
  `turn_detection.silence_duration_ms` (400 ms by default) mid-sentence gets two lines, so raise
  it for slow speakers.
- **Transcript out.** Each turn's words arrive as
  `conversation.item.input_audio_transcription.delta` events, then one `.completed` event with
  the full sentence. The delta renders inline, and the completed line replaces it with a
  timestamp. A turn that can't be transcribed ends with `.failed` instead. Transcripts arrive in
  the order the turns were spoken.

## The support agent

A transcription session never replies. For an agent that hears the caller and answers, the
support agent keeps the client and the audio path and opens a realtime session instead, with
`output_modalities: ["text"]` and `instructions`.

```bash
cd python
uv run support_agent.py
uv run support_agent.py --file call.wav
```

```bash
cd node
npm start
npm start -- --file call.wav
npm start -- --list-devices
```

The whole of what Sam knows is in `python/prompt.py`: three orders in different states
(shipped, held on a backorder, delivered), the return and cancellation policy, and a rule to
confirm the order before giving details. There are no tools, and the data sits in the prompt.

Try: "I'm calling about order N W one zero four eight two, it's under Maria Alvarez", then
"can I still cancel it?". Sam should refuse, because that order has shipped, and offer a return.

Two things the code does that the transcriber does not:

- **The greeting is seeded, not generated.** It prints locally and is also added to the
  conversation as an assistant item, so the model's history starts with it.
- **Sam's reply is held until your line has printed.** In a realtime session, the transcript of
  what the caller said can arrive after the reply has started. Printing in arrival order would
  put the answer above the question.

`node/prompt.js` is generated from `python/prompt.py`, so the two agents always share one prompt.
After editing `prompt.py`, run `uv run gen_prompt.py` from `python/`.

## Node

The Node programs read the same `.env` at the repo root. The connection is the npm SDK's
`OpenAIRealtimeWS`, given an `OpenAI` client whose `baseURL` is PolyAI's. It builds the realtime
URL and sends `Authorization: Bearer` itself. Events arrive as `rt.on("<event type>", handler)`.
The `ws` package is a peer dependency of that transport, so it is listed explicitly.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `POLY_API_KEY is not set` | No `.env`, an empty key, or a `.env` that still uses an old name, `DIALOG_API_KEY` or `DIALOGUE_API_KEY` | Copy `.env.example` to `.env` at the repo root and paste the key after `POLY_API_KEY=` |
| HTTP 401 when connecting | A missing or wrong key, or a key from a regional Studio such as `studio.us.poly.ai` | Create a key in the **API Keys** tab at [studio.poly.ai](https://studio.poly.ai). For a `studio.us.poly.ai` key, set `DIALOG_REALTIME_URL=wss://api.us.poly.ai/v1/realtime` |
| HTTP 403 when connecting | It's a personal access token | Use an API key from the **API Keys** tab |
| `unknown_parameter` or `unsupported_parameter` after `session.update` | A field the session doesn't take. A transcription session has no `instructions` or `tools`, and refuses them | Remove it. A rejected update applies none of its fields |
| One sentence prints as two lines | The speaker paused longer than `silence_duration_ms` | Raise `turn_detection.silence_duration_ms` |
| `must be mono, 16-bit, 24000 Hz` | The WAV isn't in the session's format | Convert it with `afconvert -f WAVE -d LEI16@24000 -c 1 in.wav out.wav` |
| `npm install` fails building `naudiodon2` | No C++ toolchain | On a Mac, run `xcode-select --install`, then `npm install` again |

## What this is not

No diarization, no punctuation control, no interim results beyond the streamed deltas. It is a
demonstration that a stock OpenAI Realtime client works unchanged against Dialog-RSN-1.

## License

Apache 2.0. See [LICENSE](LICENSE).
