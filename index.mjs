// divernote-claude-select: Divernote's Claude runs use claude-select accounts,
// moving to the next one when a usage limit is hit. See README.md.

import { spawn } from 'node:child_process';
import { access, constants } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createAccountUsageCollector } from './usage.mjs';

const RUNNER = fileURLToPath(new URL('./bin/claude-select-run', import.meta.url));

const selectorPath = env => env.CLAUDE_SELECT_PATH ?? join(env.HOME ?? homedir(), '.local', 'bin', 'claude-select');

async function hasSelector(selector) {
  try {
    await access(selector, constants.R_OK);
    return true;
  } catch {
    // Without claude-select there are no accounts to choose from.
    return false;
  }
}

export function waitForAccount(env) {
  // Wait before the PTY launch, outside Divernote's two-hour agent timeout.
  // Errors are handled by the normal runner, not by falling back to an
  // unwrapped Claude launch (which would bypass account selection).
  return new Promise(resolve => {
    const child = spawn(RUNNER, ['--wait-for-account'], { env, stdio: ['ignore', 'ignore', 'inherit'] });
    const stop = () => child.kill('SIGTERM');
    process.once('exit', stop);
    const done = () => {
      process.removeListener('exit', stop);
      resolve();
    };
    child.once('error', done);
    child.once('close', done);
  });
}

export default function divernoteClaudeSelect(divernote) {
  const usage = createAccountUsageCollector();
  divernote.on('status-usage', async options => (await hasSelector(selectorPath(options?.env ?? process.env)) ? usage(options) : []));
  divernote.on('agent-launch', async launch => {
    if (launch.agent !== 'claude') return undefined;
    const selector = selectorPath(launch.env);
    if (!(await hasSelector(selector))) return undefined;
    const env = { ...launch.env, CLAUDE_SELECT_PATH: selector, DIVERNOTE_LAUNCH_PURPOSE: launch.purpose };
    if (launch.purpose === 'issue') await waitForAccount(env);
    return {
      ...launch,
      command: RUNNER,
      args: [launch.command, ...launch.args],
      env,
    };
  });
}
