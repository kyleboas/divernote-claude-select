import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import extension from '../index.mjs';

function hook() {
  let handler;
  extension({ on: (name, fn) => { assert.equal(name, 'agent-launch'); handler = fn; } });
  return handler;
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
