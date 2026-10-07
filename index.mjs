// divernote-claude-select: Divernote's Claude runs use claude-select accounts,
// moving to the next one when a usage limit is hit, or on /newsub. See README.md.

import { spawn } from 'node:child_process';
import { access, constants } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const RUNNER = fileURLToPath(new URL('./bin/claude-select-run', import.meta.url));

function selectorPath(env) {
  return env.CLAUDE_SELECT_PATH ?? join(env.HOME ?? homedir(), '.local', 'bin', 'claude-select');
}

async function installed(selector) {
  try {
    await access(selector, constants.R_OK);
    return true;
  } catch {
    return false;
  }
}

/** `divernote newsub`, and /newsub on an issue: Claude moves to the next account with room. */
async function newsub({ env, stdout, stderr }) {
  const selector = selectorPath(env);
  if (!await installed(selector)) {
    stdout(`${JSON.stringify({ status: 'unavailable', detail: 'claude-select is not installed.' })}\n`);
    return 1;
  }
  return new Promise(resolve => {
    const child = spawn(RUNNER, ['--newsub'], { env: { ...env, CLAUDE_SELECT_PATH: selector }, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout.on('data', chunk => stdout(chunk.toString('utf8')));
    child.stderr.on('data', chunk => stderr(chunk.toString('utf8')));
    child.on('error', error => {
      stdout(`${JSON.stringify({ status: 'unavailable', detail: `claude-select-run did not start: ${error.message}` })}\n`);
      resolve(1);
    });
    child.on('close', code => resolve(code ?? 1));
  });
}

export default function divernoteClaudeSelect(divernote) {
  divernote.on('agent-launch', async launch => {
    if (launch.agent !== 'claude') return undefined;
    const selector = selectorPath(launch.env);
    // Without claude-select there are no accounts to choose from.
    if (!await installed(selector)) return undefined;
    return {
      ...launch,
      command: RUNNER,
      args: [launch.command, ...launch.args],
      env: { ...launch.env, CLAUDE_SELECT_PATH: selector, DIVERNOTE_LAUNCH_PURPOSE: launch.purpose },
    };
  });
  divernote.registerCommand?.('newsub', newsub);
}
