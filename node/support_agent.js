#!/usr/bin/env node
// A voice-in, text-out support agent on Dialog-RSN-1, through the OpenAI Realtime SDK (Node).
//
// Speak, and Sam from Northwind Outfitters answers in text. Everything Sam knows is in
// prompt.js: three orders and the return policy, all in the instructions, no tools.
//
//   npm start                              microphone, us-1 production
//   npm start -- --file call.wav           a 24 kHz mono PCM16 WAV instead of the mic
//   npm start -- --list-devices            pick a microphone by name or index

import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { setTimeout as sleep } from "node:timers/promises";
import naudiodon from "naudiodon2";
import OpenAI from "openai";
import { OpenAIRealtimeWS } from "openai/realtime/ws";

import { GREETING, INSTRUCTIONS } from "./prompt.js";

const MODEL = "dialog-rsn-1";
const DEFAULT_URL = "https://api.us.poly.ai/v1";

// The protocol's one sample rate. The mic opens at it, so nothing resamples anywhere.
const SAMPLE_RATE = 24_000;
const CHUNK_MS = 100;
const CHUNK_FRAMES = (SAMPLE_RATE * CHUNK_MS) / 1000;

const SESSION = {
  type: "realtime",
  output_modalities: ["text"],
  instructions: INSTRUCTIONS,
  audio: {
    input: {
      format: { type: "audio/pcm", rate: SAMPLE_RATE },
      turn_detection: { type: "server_vad", create_response: true },
    },
  },
};

// The .env next to this folder's parent is the shared one; a local .env wins if present.
for (const path of ["../.env", ".env"]) {
  try {
    process.loadEnvFile(new URL(path, import.meta.url));
  } catch {
    // no such file, fine
  }
}

const stamp = () => new Date().toTimeString().slice(0, 8);
const CLEAR = "\r\x1b[K";

function listDevices() {
  for (const dev of naudiodon.getDevices()) {
    if (dev.maxInputChannels > 0) console.log(`${String(dev.id).padStart(3)}  ${dev.name}`);
  }
}

function pickDevice(name) {
  if (name === undefined) return -1;
  if (/^\d+$/.test(name)) return Number(name);
  const match = naudiodon
    .getDevices()
    .find((d) => d.maxInputChannels > 0 && d.name.toLowerCase().includes(name.toLowerCase()));
  if (!match) {
    console.error(`no input device matching ${JSON.stringify(name)}; try --list-devices`);
    process.exit(1);
  }
  return match.id;
}

/** Capture the microphone and append it to the input buffer, one chunk at a time. */
function pumpMicrophone(rt, deviceId) {
  const io = new naudiodon.AudioIO({
    inOptions: {
      channelCount: 1,
      sampleFormat: naudiodon.SampleFormat16Bit,
      sampleRate: SAMPLE_RATE,
      deviceId,
      framesPerBuffer: CHUNK_FRAMES,
      closeOnError: true,
    },
  });
  io.on("data", (buf) => {
    rt.send({ type: "input_audio_buffer.append", audio: buf.toString("base64") });
  });
  io.on("error", (err) => console.error(`[audio] ${err.message}`));
  io.start();
  return () => io.quit();
}

/** The PCM16 payload of a WAV file, checked for the rate and shape the session declares. */
function readWav(path) {
  const buf = readFileSync(path);
  if (buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error(`${path} is not a WAV file`);
  }
  let offset = 12;
  let format = null;
  while (offset + 8 <= buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (id === "fmt ") {
      format = {
        channels: buf.readUInt16LE(body + 2),
        rate: buf.readUInt32LE(body + 4),
        bits: buf.readUInt16LE(body + 14),
      };
    } else if (id === "data") {
      if (!format || format.channels !== 1 || format.bits !== 16 || format.rate !== SAMPLE_RATE) {
        throw new Error(
          `${path} must be mono, 16-bit, ${SAMPLE_RATE} Hz. Convert with: ` +
            `afconvert -f WAVE -d LEI16@${SAMPLE_RATE} -c 1 in.wav out.wav`,
        );
      }
      return buf.subarray(body, body + size);
    }
    offset = body + size + (size % 2);
  }
  throw new Error(`${path} has no data chunk`);
}

/** Stream a WAV file at real time, as if it were being spoken now. */
async function pumpFile(rt, path) {
  const pcm = readWav(path);
  const bytesPerChunk = CHUNK_FRAMES * 2;
  const started = performance.now();
  let sent = 0;
  for (let at = 0; at < pcm.length; at += bytesPerChunk) {
    const chunk = pcm.subarray(at, at + bytesPerChunk);
    rt.send({ type: "input_audio_buffer.append", audio: chunk.toString("base64") });
    sent += chunk.length / 2;
    // Hold to the file's own pace, so server-side VAD sees natural gaps.
    await sleep(Math.max(0, started + (sent / SAMPLE_RATE) * 1000 - performance.now()));
  }
  // A trailing second of silence lets the VAD close the last turn.
  const silence = Buffer.alloc(bytesPerChunk).toString("base64");
  for (let i = 0; i < 10; i++) {
    rt.send({ type: "input_audio_buffer.append", audio: silence });
    await sleep(CHUNK_MS);
  }
}

/**
 * Render the call: what you said, then Sam's reply.
 *
 * The model emits its reply before the transcript of what it heard, since the transcript is the
 * last section of its output. The reply is held back until the transcript has printed, so the
 * terminal reads in the order the conversation happened.
 */
function renderConversation(rt) {
  let heard = "";
  let reply = "";
  let replyShown = false;
  const out = (s) => process.stdout.write(s);

  rt.on("session.updated", () => {
    console.log(`[connected to ${MODEL}]  speak when ready, Ctrl-C to quit\n`);
    console.log(`${stamp()}  Sam:  ${GREETING}\n`);
  });
  rt.on("input_audio_buffer.speech_started", () => out("…"));
  rt.on("conversation.item.input_audio_transcription.delta", (e) => {
    heard += e.delta;
  });
  rt.on("conversation.item.input_audio_transcription.completed", (e) => {
    out(`${CLEAR}${stamp()}  You:  ${(e.transcript || heard).trim()}\n`);
    heard = "";
    if (reply) {
      out(`${stamp()}  Sam:  ${reply}`);
      replyShown = true;
    }
  });
  rt.on("response.output_text.delta", (e) => {
    reply += e.delta;
    if (replyShown) out(e.delta);
  });
  rt.on("response.output_text.done", (e) => {
    if (!replyShown) out(`${CLEAR}${stamp()}  Sam:  ${e.text}`);
    out("\n\n");
    reply = "";
    replyShown = false;
  });
  rt.on("error", (err) => {
    console.error(`[error] ${err.message}`);
  });
}

async function main() {
  const { values: args } = parseArgs({
    options: {
      url: { type: "string" },
      file: { type: "string" },
      device: { type: "string" },
      "list-devices": { type: "boolean", default: false },
    },
  });
  if (args["list-devices"]) return listDevices();

  const apiKey = process.env.DIALOGUE_API_KEY;
  if (!apiKey) {
    console.error("DIALOGUE_API_KEY is not set. Copy ../.env.example to ../.env and fill it in.");
    process.exit(1);
  }
  const baseURL = args.url || process.env.DIALOGUE_API_URL || DEFAULT_URL;

  // The SDK builds wss://<host>/v1/realtime?model=... from the client's base URL and sends the
  // key as Authorization: Bearer. Nothing else about the connection is ours.
  const client = new OpenAI({ apiKey, baseURL });
  const rt = new OpenAIRealtimeWS({ model: MODEL }, client);
  console.log(`connecting to ${rt.url} …`);

  renderConversation(rt);
  await new Promise((resolve, reject) => {
    rt.socket.once("open", resolve);
    rt.socket.once("error", reject);
  });
  rt.send({ type: "session.update", session: SESSION });
  // The greeting is printed locally rather than generated, so the model's history starts with
  // it too: an assistant message the model can refer back to.
  rt.send({
    type: "conversation.item.create",
    item: {
      type: "message",
      role: "assistant",
      content: [{ type: "output_text", text: GREETING }],
    },
  });

  const closed = new Promise((resolve) => rt.socket.once("close", resolve));
  if (args.file) {
    await pumpFile(rt, args.file);
    // Give the last turn time to come back before closing.
    await Promise.race([closed, sleep(20_000)]);
    rt.close();
  } else {
    const stopMic = pumpMicrophone(rt, pickDevice(args.device));
    process.once("SIGINT", () => {
      console.log("\nbye");
      stopMic();
      rt.close();
      process.exit(0);
    });
    await closed;
  }
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
