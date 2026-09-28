// divernote-claude-select: Divernote's Claude runs use claude-select accounts,
// moving to the next one when a usage limit is hit. See README.md.

import { access, constants } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const RUNNER = fileURLToPath(new URL('./bin/claude-select-run', import.meta.url));

export default function divernoteClaudeSelect(divernote) {
  divernote.on('agent-launch', async launch => {
    if (launch.agent !== 'claude') return undefined;
    const selector = launch.env.CLAUDE_SELECT_PATH ?? join(launch.env.HOME ?? homedir(), '.local', 'bin', 'claude-select');
    try {
      await access(selector, constants.R_OK);
    } catch {
      // Without claude-select there are no accounts to choose from.
      return undefined;
    }
    return {
      ...launch,
      command: RUNNER,
      args: [launch.command, ...launch.args],
      env: { ...launch.env, CLAUDE_SELECT_PATH: selector, DIVERNOTE_LAUNCH_PURPOSE: launch.purpose },
    };
  });
}
