# divernote-claude-select

A Divernote extension. When a Claude account hits its usage limit, Divernote's
Claude runs move to the next available [claude-select](#requirements) account.

## Install

Clone this repository into Divernote's extensions folder, the same way you
would add a Pi extension to `~/.pi/agent/extensions`:

```sh
git clone git@github.com:kyleboas/divernote-claude-select.git \
  ~/.config/divernotes/extensions/divernote-claude-select
launchctl kickstart -k gui/$(id -u)/com.divernote.pi-work
```

Divernote loads every `~/.config/divernotes/extensions/<name>/index.mjs` when
it starts. To remove the extension, delete the folder and restart the worker.

## Requirements

- Divernote with the `agent-launch` extension hook. `/newsub` also needs a
  Divernote that offers extension commands on an issue.
- `claude-select` at `~/.local/bin/claude-select`, or at `CLAUDE_SELECT_PATH`.
  Accounts are claude-select's (`~/.claude-account1`…`4`, or
  `CLAUDE_SELECT_ACCOUNTS`), and each account's `projects` folder links to the
  shared `~/.claude/projects`.
- `python3` 3.9 or later.

If claude-select is missing, the extension does nothing.

## How it works

- `index.mjs` registers an `agent-launch` hook. Each Claude launch Divernote
  makes runs through `bin/claude-select-run`, with the real command as the
  first argument. Pi launches are not changed.
- Each run uses the current account (`divernote_current` in
  `~/.claude-account-state.json`), as long as it is logged in and not cooling
  down. Otherwise it uses the next account that is.
- When native Claude auth misses file-backed credentials, the runner uses
  that account's saved OAuth session. An expired access token is refreshed
  before rejecting the account. Rotated credentials are saved atomically
  with owner-only permissions; concurrent issues serialize refreshes.
- The runner watches the session transcript while Claude runs. When Claude
  says a limit was hit, the account cools down until the reset time Claude
  states. If Claude only warned that the limit is near, the cooldown is one
  hour. claude-select's menu shows the same cooldowns.
- If another account has room, the runner tells the issue to continue
  (`divernote pi-message`). It then ends a run that Claude left idle at a
  hard limit, so Divernote does not wait out its two-hour timeout. The next
  run resumes the same session on the next account.
- A Claude terminal you open on an issue that Claude leaves idle at a hard
  limit is reopened in place on the next account with room. It resumes the
  same session and is told to continue. If no other account has room, or you
  quit Claude yourself, the terminal is left alone and only its account is put
  in cooldown.
- `/newsub` on a Claude Code issue in Divernote (or `divernote newsub`)
  makes the next account with room current, without cooling the old one
  down. Divernote then continues the issue there: a running issue restarts
  on the same session, and any other issue is sent the message typed after
  `/newsub`, or a continue message. If no other account has room, it says
  which one resets first and changes nothing. It needs a Divernote with
  `/newsub`.
- If every account is cooling down, the run uses the account that resets
  first, and nothing is queued.
- Log: `rotate.log` in this folder.

## Tests

```sh
python3 -m unittest discover -s test
node --test test/index.test.mjs
```
