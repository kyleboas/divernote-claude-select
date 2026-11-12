// divernote-claude-select: Divernote's Claude runs use claude-select accounts,
// moving to the next one when a usage limit is hit. See README.md.

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

export default function divernoteClaudeSelect(divernote) {
  const usage = createAccountUsageCollector();
  divernote.on('status-usage', async options => (await hasSelector(selectorPath(options?.env ?? process.env)) ? usage(options) : []));
  divernote.on('agent-launch', async launch => {
    if (launch.agent !== 'claude') return undefined;
    const selector = selectorPath(launch.env);
    if (!(await hasSelector(selector))) return undefined;
    return {
      ...launch,
      command: RUNNER,
      args: [launch.command, ...launch.args],
      env: { ...launch.env, CLAUDE_SELECT_PATH: selector, DIVERNOTE_LAUNCH_PURPOSE: launch.purpose },
    };
  });
}
