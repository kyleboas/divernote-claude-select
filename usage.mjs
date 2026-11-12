// Status usage for every claude-select account: each account's 5-hour and
// weekly limits, read with the login that account already has. Read-only: no
// token refresh, no credential or state writes. Only quota figures leave here.

import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { homedir, platform } from 'node:os';
import { join, resolve } from 'node:path';

const MAX_BYTES = 64 * 1024;
const MAX_ACCOUNTS = 4;
const ID = /^[a-z][a-z0-9-]{0,23}$/;
const PLANS = { max: 'Max', pro: 'Pro', team: 'Team', enterprise: 'Enterprise' };
const LIMITS = [['five_hour', '5h'], ['seven_day', 'Week'], ['seven_day_opus', 'Week Opus'], ['seven_day_sonnet', 'Week Sonnet']];

const percent = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100;
const tokenText = value => typeof value === 'string' && value.length > 0 && value.length < 16_384 && !/[\r\n]/.test(value);
const timestamp = value => {
  if (typeof value !== 'string' && typeof value !== 'number') return undefined;
  const millis = typeof value === 'number' ? value * 1000 : Date.parse(value);
  return Number.isFinite(millis) && millis >= 0 && millis <= 8.64e15 ? new Date(millis).toISOString() : undefined;
};

export function parseClaudeUsage(data) {
  return LIMITS.flatMap(([key, name]) => {
    const value = data?.[key];
    if (!percent(value?.utilization)) return [];
    const resetsAt = timestamp(value.resets_at);
    return [{ name, used: value.utilization, ...(resetsAt ? { resetsAt } : {}) }];
  });
}

/** claude-select's accounts: CLAUDE_SELECT_ACCOUNTS, else ~/.claude-account1…4. */
export function selectAccounts(env, home) {
  const expand = path => resolve(path.startsWith('~/') ? join(home, path.slice(2)) : path);
  const raw = env.CLAUDE_SELECT_ACCOUNTS;
  const accounts = raw
    ? raw.split(',').map(item => item.trim()).filter(Boolean).map((item, index) => {
      const at = item.indexOf('=');
      return at < 0 ? [`claude${index + 1}`, expand(item)] : [item.slice(0, at).trim(), expand(item.slice(at + 1).trim())];
    })
    : [1, 2, 3, 4].map(i => [`claude${i}`, join(home, `.claude-account${i}`)]);
  return accounts.filter(([name]) => ID.test(name)).slice(0, MAX_ACCOUNTS);
}

async function readJson(path) {
  try {
    if ((await stat(path)).size > MAX_BYTES) return undefined;
    return JSON.parse(await readFile(path, 'utf8'));
  } catch { return undefined; }
}

// Claude Code names a custom config dir's keychain item with a short hash of that dir.
export const keychainService = configDir => `Claude Code-credentials-${createHash('sha256').update(configDir).digest('hex').slice(0, 8)}`;

function keychain(service) {
  return new Promise(done => {
    execFile('/usr/bin/security', ['find-generic-password', '-s', service, '-w'],
      { timeout: 700, maxBuffer: MAX_BYTES }, (error, stdout) => {
        try { done(error ? undefined : JSON.parse(stdout)); } catch { done(undefined); }
      });
  });
}

// Never follow a redirect with a credential or pass response text to Status.
async function quota(fetcher, token, signal) {
  const response = await fetcher('https://api.anthropic.com/api/oauth/usage', {
    headers: { Authorization: `Bearer ${token}`, 'anthropic-beta': 'oauth-2025-04-20', Accept: 'application/json' },
    redirect: 'error', signal,
  });
  if (!response.ok) {
    await response.body?.cancel();
    return { state: response.status === 401 || response.status === 403 ? 'signed-out' : response.status === 429 ? 'rate-limited' : 'unavailable' };
  }
  const source = await response.text();
  if (Buffer.byteLength(source) > MAX_BYTES) return { state: 'unavailable' };
  return { data: JSON.parse(source) };
}

export function createAccountUsageCollector({
  fetch: fetcher = globalThis.fetch, read = readJson, readKeychain = keychain, os = platform(), home = homedir(),
  clock = Date.now, timeoutMs = 2100,
} = {}) {
  let cached;
  let pending;
  return async function collect({ env = process.env, signal } = {}) {
    const root = env.HOME || home;
    const accounts = selectAccounts(env, root);
    const statePath = env.CLAUDE_SELECT_STATE || join(root, '.claude-account-state.json');
    const key = JSON.stringify([accounts, statePath]);
    if (cached?.key === key && clock() - cached.time < 60_000) return cached.reports;
    if (pending?.key === key) return pending.request;
    const run = async () => {
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal?.addEventListener('abort', abort, { once: true });
      if (signal?.aborted) controller.abort();
      const timer = setTimeout(abort, timeoutMs);
      const at = new Date(clock()).toISOString();
      const state = (await read(statePath)) ?? {};
      try {
        return await Promise.all(accounts.map(async ([name, dir]) => {
          const cooling = Number(state.accounts?.[name]?.cooldown_until) * 1000 > clock();
          const note = state.divernote_current === name ? ' · in use' : cooling ? ' · cooling down' : '';
          const report = (plan, result) => ({ id: name, plan: `${plan}${note}`, at, state: result.state || (result.limits?.length ? 'ok' : 'unavailable'), limits: result.limits || [] });
          let plan = 'Claude';
          try {
            // macOS keeps the live, refreshed login in the keychain; the file can be a stale copy.
            const logins = [];
            if (os === 'darwin') logins.push((await readKeychain(keychainService(dir)))?.claudeAiOauth);
            logins.push((await read(join(dir, '.credentials.json')))?.claudeAiOauth);
            const usable = logins.filter(item => tokenText(item?.accessToken));
            const auth = usable.find(item => !(item.expiresAt > 0 && item.expiresAt <= clock())) ?? usable[0];
            if (!auth) return report(plan, { state: 'signed-out' });
            if (typeof auth.subscriptionType === 'string' && Object.hasOwn(PLANS, auth.subscriptionType)) plan = PLANS[auth.subscriptionType];
            const result = await quota(fetcher, auth.accessToken, controller.signal);
            return report(plan, { state: result.state, limits: parseClaudeUsage(result.data) });
          } catch {
            return report(plan, { state: 'unavailable' });
          }
        }));
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
      }
    };
    const request = run();
    pending = { key, request };
    try {
      const reports = await request;
      cached = { key, time: clock(), reports };
      return reports;
    } finally {
      if (pending?.request === request) pending = undefined;
    }
  };
}
