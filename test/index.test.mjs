import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import extension from '../index.mjs';

function registered() {
  let handler;
  const commands = {};
  extension({ on: (name, fn) => { assert.equal(name, 'agent-launch'); handler = fn; }, registerCommand: (name, fn) => { commands[name] = fn; } });
  return { handler, commands };
}

function hook() {
  return registered().handler;
}

test('Claude launches run through claude-select-run; Pi launches are unchanged', async t => {
  const home = await mkdtemp(join(tmpdir(), 'dcs-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const selector = join(home, 'claude-select');
  await writeFile(selector, '');
  const launch = { agent: 'claude', purpose: 'issue', command: 'claude', args: ['--resume', 'id'], cwd: '/w', sessionId: 'id', env: { HOME: home, CLAUDE_SELECT_PATH: selector } };
  const result = await hook()(launch);
  assert.equal(result.command, fileURLToPath(new URL('../bin/claude-select-run', import.meta.url)));
  assert.deepEqual(result.args, ['claude', '--resume', 'id']);
  assert.equal(result.env.DIVERNOTE_LAUNCH_PURPOSE, 'issue');
  assert.equal(await hook()({ ...launch, agent: 'pi' }), undefined);
  assert.equal(await hook()({ ...launch, env: { HOME: home, CLAUDE_SELECT_PATH: join(home, 'missing') } }), undefined);
});

test('newsub reports that it cannot switch without claude-select', async t => {
  const home = await mkdtemp(join(tmpdir(), 'dcs-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  let written = '';
  const code = await registered().commands.newsub({ argv: ['--json'], env: { HOME: home, CLAUDE_SELECT_PATH: join(home, 'missing') }, stdout: text => { written += text; }, stderr: () => {} });
  assert.equal(code, 1);
  assert.deepEqual(JSON.parse(written), { status: 'unavailable', detail: 'claude-select is not installed.' });
});
