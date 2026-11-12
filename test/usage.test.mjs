import assert from 'node:assert/strict';
import test from 'node:test';

import { createAccountUsageCollector, keychainService, selectAccounts } from '../usage.mjs';

const NOW = Date.parse('2026-11-12T12:00:00Z');
const json = (status, body) => ({ ok: status < 400, status, body: { cancel: async () => {} }, text: async () => JSON.stringify(body) });

test('accounts follow claude-select: the default four or CLAUDE_SELECT_ACCOUNTS', () => {
  assert.deepEqual(selectAccounts({}, '/h').map(a => a[0]), ['claude1', 'claude2', 'claude3', 'claude4']);
  assert.deepEqual(selectAccounts({ CLAUDE_SELECT_ACCOUNTS: 'work=~/w, /p,Bad=/x' }, '/h'), [['work', '/h/w'], ['claude2', '/p']]);
});

test('each account reports its own limits, plan and whether it is in use or cooling down', async () => {
  const tokens = { '/h/.claude-account1': 'one', '/h/.claude-account2': 'two', '/h/.claude-account3': 'three' };
  const seen = [];
  const collect = createAccountUsageCollector({
    os: 'darwin', home: '/h', clock: () => NOW,
    readKeychain: async service => {
      const dir = Object.keys(tokens).find(d => keychainService(d) === service);
      return dir && dir !== '/h/.claude-account3' ? { claudeAiOauth: { accessToken: tokens[dir], subscriptionType: dir.endsWith('1') ? 'max' : 'pro', expiresAt: NOW + 1000 } } : undefined;
    },
    read: async path => path === '/h/.claude-account-state.json'
      ? { divernote_current: 'claude1', accounts: { claude2: { cooldown_until: NOW / 1000 + 60 } } }
      : path === '/h/.claude-account3/.credentials.json' ? { claudeAiOauth: { accessToken: 'three' } } : undefined,
    fetch: async (url, { headers, redirect }) => {
      assert.equal(redirect, 'error');
      seen.push(headers.Authorization);
      if (headers.Authorization === 'Bearer three') return json(401, {});
      return json(200, { five_hour: { utilization: 42, resets_at: '2026-11-12T14:00:00Z' }, seven_day: { utilization: 7 } });
    },
  });
  const reports = await collect({ env: { HOME: '/h' } });
  assert.deepEqual(reports, [
    { id: 'claude1', plan: 'Max · in use', at: '2026-11-12T12:00:00.000Z', state: 'ok', limits: [{ name: '5h', used: 42, resetsAt: '2026-11-12T14:00:00.000Z' }, { name: 'Week', used: 7 }] },
    { id: 'claude2', plan: 'Pro · cooling down', at: '2026-11-12T12:00:00.000Z', state: 'ok', limits: [{ name: '5h', used: 42, resetsAt: '2026-11-12T14:00:00.000Z' }, { name: 'Week', used: 7 }] },
    { id: 'claude3', plan: 'Claude', at: '2026-11-12T12:00:00.000Z', state: 'signed-out', limits: [] },
    { id: 'claude4', plan: 'Claude', at: '2026-11-12T12:00:00.000Z', state: 'signed-out', limits: [] },
  ]);
  assert.deepEqual(seen.sort(), ['Bearer one', 'Bearer three', 'Bearer two']);
  // Cached for a minute: no new requests.
  await collect({ env: { HOME: '/h' } });
  assert.equal(seen.length, 3);
});

test('a failed request is unavailable, never a guess', async () => {
  const collect = createAccountUsageCollector({
    os: 'linux', home: '/h', clock: () => NOW,
    read: async path => path.endsWith('.credentials.json') ? { claudeAiOauth: { accessToken: 't' } } : undefined,
    fetch: async () => { throw new Error('secret'); },
  });
  for (const report of await collect({ env: { HOME: '/h', CLAUDE_SELECT_ACCOUNTS: '/a' } })) assert.equal(report.state, 'unavailable');
});
