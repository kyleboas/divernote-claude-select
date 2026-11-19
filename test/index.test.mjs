import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import extension from '../index.mjs';

function hook() {
  let handler;
  extension({ on: (name, fn) => { if (name === 'agent-launch') handler = fn; } });
  return handler;
}

async function fixture(t, delay = 0) {
  const home = await mkdtemp(join(tmpdir(), 'dcs-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const selector = join(home, 'claude-select');
  const state = join(home, 'state.json');
  await writeFile(selector, `
import json, os
from pathlib import Path
from contextlib import nullcontext
accounts = [('claude1', 'unused')]
state_lock = nullcontext
def load_state():
    return json.loads(Path(os.environ['TEST_STATE']).read_text())
def save_state(st):
    raise AssertionError('preflight must not record a launch')
`);
  await writeFile(join(home, 'claude'), '#!/bin/sh\necho \'{"loggedIn":true}\'\n', { mode: 0o755 });
  await writeFile(state, JSON.stringify({ accounts: { claude1: { cooldown_until: Date.now() / 1000 + delay } } }));
  return {
    agent: 'claude', purpose: 'issue', command: 'claude', args: ['--resume', 'id'], cwd: home, sessionId: 'id',
    env: { ...process.env, HOME: home, CLAUDE_SELECT_PATH: selector, TEST_STATE: state,
      DIVERNOTE_CLAUDE_SELECT_LOG: join(home, 'rotate.log'), PATH: `${home}:${process.env.PATH}` },
  };
}

test('Claude launches run through claude-select-run; Pi launches are unchanged', async t => {
  const launch = await fixture(t);
  const result = await hook()(launch);
  assert.equal(result.command, fileURLToPath(new URL('../bin/claude-select-run', import.meta.url)));
  assert.deepEqual(result.args, ['claude', '--resume', 'id']);
  assert.equal(result.env.DIVERNOTE_LAUNCH_PURPOSE, 'issue');
  assert.equal(await hook()({ ...launch, agent: 'pi' }), undefined);
  assert.equal(await hook()({ ...launch, env: { HOME: launch.env.HOME, CLAUDE_SELECT_PATH: join(launch.env.HOME, 'missing') } }), undefined);
});

test('the issue launch is held until reset, before Divernote starts its PTY timeout', async t => {
  const launch = await fixture(t, 2);
  const before = await readFile(launch.env.TEST_STATE, 'utf8');
  const exitListeners = process.listenerCount('exit');
  let ready = false;
  const pending = hook()(launch).then(result => { ready = true; return result; });
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(ready, false);
  const result = await pending;
  assert.ok(Date.now() / 1000 >= JSON.parse(before).accounts.claude1.cooldown_until);
  assert.equal(result.command, fileURLToPath(new URL('../bin/claude-select-run', import.meta.url)));
  assert.equal(await readFile(launch.env.TEST_STATE, 'utf8'), before);
  assert.equal(process.listenerCount('exit'), exitListeners);
});

test('terminal launches leave waiting to the interactive runner', async t => {
  const launch = await fixture(t, 3600);
  const result = await hook()({ ...launch, purpose: 'terminal' });
  assert.equal(result.env.DIVERNOTE_LAUNCH_PURPOSE, 'terminal');
  await assert.rejects(readFile(launch.env.DIVERNOTE_CLAUDE_SELECT_LOG), { code: 'ENOENT' });
});

test('failed preflight still uses the runner instead of bypassing account selection', async t => {
  const launch = await fixture(t);
  await writeFile(launch.env.CLAUDE_SELECT_PATH, 'raise SystemExit(1)\n');
  const result = await hook()(launch);
  assert.equal(result.command, fileURLToPath(new URL('../bin/claude-select-run', import.meta.url)));
});
