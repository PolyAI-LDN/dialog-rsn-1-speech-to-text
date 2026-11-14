const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

// Exercise the renderer without opening the native microphone or a network client.
const source = readFileSync(join(__dirname, 'support_agent.js'), 'utf8');
const renderer = source.slice(source.indexOf('function renderConversation(rt) {'), source.indexOf('\nasync function main()'));

test('a completed reply stays below its caller transcript', () => {
  let output = '';
  const render = vm.runInNewContext(`(${renderer})`, {
    stamp: () => '12:00:00', CLEAR: '', GREETING: '', MODEL: 'test',
    process: { stdout: { write(text) { output += text; } } },
    console: { log(text) { output += text + '\n'; }, error() {} },
  });
  const events = new EventEmitter();
  render(events);
  events.emit('response.created', {});
  events.emit('response.output_text.delta', { delta: 'Order shipped.' });
  events.emit('response.output_text.done', { text: 'Order shipped.' });
  events.emit('conversation.item.input_audio_transcription.completed', { transcript: 'Where is my order?' });
  events.emit('response.done', {response:{status:'completed'}});
  assert.ok(output.indexOf('You:') < output.indexOf('Sam:'), output);
  assert.equal(output.match(/Order shipped\./g)?.length, 1);
});

test('completed response.done can precede the caller transcript', () => {
  let output = '';
  const render = vm.runInNewContext(`(${renderer})`, {
    stamp: () => '12:00:00', CLEAR: '', GREETING: '', MODEL: 'test',
    process: { stdout: { write(text) { output += text; } } }, console: { log() {}, error() {} },
  });
  const events = new EventEmitter();render(events);
  events.emit('response.created', {});
  events.emit('response.output_text.delta', {delta:'Order shipped.'});
  events.emit('response.output_text.done', {text:'Order shipped.'});
  events.emit('response.done', {response:{status:'completed'}});
  assert.equal(output,'');
  events.emit('conversation.item.input_audio_transcription.completed', {transcript:'Where is my order?'});
  assert.ok(output.indexOf('You:')<output.indexOf('Sam:'),output);
});
test('an early transcript survives response.created', () => {
  let output = '';
  const render = vm.runInNewContext(`(${renderer})`, {
    stamp: () => '12:00:00', CLEAR: '', GREETING: '', MODEL: 'test',
    process: { stdout: { write(text) { output += text; } } }, console: { log() {}, error() {} },
  });
  const events = new EventEmitter();render(events);
  events.emit('conversation.item.input_audio_transcription.completed', {transcript:'Where is my order?'});
  events.emit('response.created', {});
  events.emit('response.output_text.delta', {delta:'Order shipped.'});
  events.emit('response.output_text.done', {text:'Order shipped.'});
  assert.match(output,/Sam:  Order shipped[.]/);
  events.emit('response.done', {response:{status:'completed'}});
  assert.equal(output.match(/Order shipped[.]/g)?.length,1);
});
