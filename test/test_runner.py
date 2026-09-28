import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "bin" / "claude-select-run"
SID = "22222222-2222-4333-8444-555555555555"
LIMIT = "You've hit your session limit · resets 6:20pm (America/New_York)"

FAKE_CLAUDE = r"""#!/bin/sh
echo "claude $CLAUDE_CONFIG_DIR" >> "$CALLS"
trap 'echo "claude got TERM" >> "$CALLS"; [ -n "$idle" ] && kill "$idle"; exit 0' TERM
sid=""; while [ $# -gt 0 ]; do case "$1" in --session-id|--resume) sid="$2";; esac; shift; done
dir="$CLAUDE_CONFIG_DIR/projects/$(pwd | sed 's/[^A-Za-z0-9]/-/g')"; mkdir -p "$dir"
if [ "$CLAUDE_CONFIG_DIR" = "$LIMITED" ]; then
  sleep 1
  ts=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  printf '{"type":"assistant","timestamp":"%s","isApiErrorMessage":true,"error":"rate_limit","message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}\n' "$ts" "$LIMIT_TEXT" >> "$dir/$sid.jsonl"
  [ -n "$HANG" ] && { sleep 600 >/dev/null 2>&1 & idle=$!; wait; }
fi
exit 0
"""

FAKE_DIVERNOTE = """#!/bin/sh
echo "divernote $*" >> "$CALLS"
echo '{"status":"later"}'
"""


def load_runner():
    loader = importlib.machinery.SourceFileLoader("claude_select_run", str(RUNNER))
    spec = importlib.util.spec_from_loader("claude_select_run", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.accounts = [self.tmp / name for name in ("a1", "a2", "a3")]
        for account in self.accounts[:2]:
            account.mkdir()
            (account / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x", "refreshToken": "r", "expiresAt": 1}}))
        fakes = self.tmp / "fakes"
        fakes.mkdir()
        for name, body in (("claude", FAKE_CLAUDE), ("divernote", FAKE_DIVERNOTE)):
            (fakes / name).write_text(body)
            (fakes / name).chmod(0o755)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.calls = self.tmp / "calls.log"
        self.env = {
            **os.environ,
            "PATH": f"{fakes}:/usr/bin:/bin",
            "CALLS": str(self.calls),
            "LIMITED": str(self.accounts[0]),
            "LIMIT_TEXT": LIMIT,
            "CLAUDE_SELECT_STATE": str(self.tmp / "state.json"),
            "CLAUDE_SELECT_ACCOUNTS": ",".join(f"claude{i + 1}={path}" for i, path in enumerate(self.accounts)),
            "CLAUDE_SELECT_SHARED_PROJECTS": str(self.tmp / "shared"),
            "DIVERNOTE_CLAUDE_SELECT_LOG": str(self.tmp / "rotate.log"),
        }

    def run_claude(self, *args, purpose="issue", **extra):
        env = {**self.env, **extra, "DIVERNOTE_LAUNCH_PURPOSE": purpose}
        started = time.time()
        subprocess.run([str(RUNNER), str(self.tmp / "fakes" / "claude"), *args], cwd=self.work, env=env, check=True, timeout=60)
        return time.time() - started

    def calls_made(self):
        return self.calls.read_text().replace(str(self.tmp) + "/", "").splitlines()

    def state(self):
        return json.loads((self.tmp / "state.json").read_text())

    def test_a_limit_moves_the_next_run_to_the_next_account_and_continues_the_issue(self):
        self.run_claude("--session-id", SID, "--", "hi")
        self.run_claude("--resume", SID, "--", "go")
        calls = self.calls_made()
        self.assertEqual(calls[0], "claude a1")
        self.assertTrue(calls[1].startswith(f"divernote pi-message --session-id {SID} --text Your last turn was cut off"))
        self.assertEqual(calls[2:], ["claude a2"])
        state = self.state()
        self.assertEqual(state["divernote_current"], "claude2")
        self.assertGreater(state["accounts"]["claude1"]["cooldown_until"], time.time())
        self.assertEqual(state["accounts"]["claude2"]["cooldown_until"], 0)
        # The account it moved to trusts the issue folder, as Divernote's own does.
        trusted = json.loads((self.accounts[1] / ".claude.json").read_text())["projects"]
        self.assertTrue(trusted[str(self.work.resolve())]["hasTrustDialogAccepted"])

    def test_a_run_left_idle_at_a_hard_limit_is_ended_after_the_continue_is_queued(self):
        elapsed = self.run_claude("--session-id", SID, "--", "hi", HANG="1")
        self.assertLess(elapsed, 20)
        calls = self.calls_made()
        self.assertEqual(calls[0], "claude a1")
        self.assertTrue(calls[1].startswith("divernote pi-message"))
        self.assertEqual(calls[2], "claude got TERM")

    def test_a_terminal_the_user_opened_is_neither_continued_nor_ended(self):
        self.run_claude("--resume", SID, purpose="terminal")
        self.assertEqual(self.calls_made(), ["claude a1"])
        self.assertGreater(self.state()["accounts"]["claude1"]["cooldown_until"], time.time())

    def test_nothing_is_queued_when_no_other_account_has_room(self):
        (self.accounts[1] / ".credentials.json").unlink()
        self.run_claude("--session-id", SID, "--", "hi")
        self.assertEqual(self.calls_made(), ["claude a1"])

    def test_an_earlier_limit_in_the_shared_transcript_is_not_blamed_on_this_run(self):
        self.run_claude("--session-id", SID, "--", "hi")
        self.run_claude("--resume", SID, "--", "go")
        self.run_claude("--resume", SID, "--", "again")
        self.assertEqual([call for call in self.calls_made() if call.startswith("claude")], ["claude a1", "claude a2", "claude a2"])
        self.assertEqual(self.state()["accounts"]["claude2"]["cooldown_until"], 0)


class ResetTime(unittest.TestCase):
    def test_reset_times_claude_states(self):
        runner = load_runner()
        now = time.mktime((2026, 9, 28, 17, 50, 0, 0, 0, -1))
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        fmt = lambda text: time.strftime("%m-%d %H:%M", time.localtime(runner.reset_time(text, now)))
        self.assertEqual(fmt("resets 6:20pm (America/New_York)"), "09-28 18:20")
        self.assertEqual(fmt("resets 8pm (America/New_York)"), "09-28 20:00")
        self.assertEqual(fmt("resets 3am (America/New_York)"), "09-29 03:00")
        self.assertEqual(fmt("resets Oct 2 at 8pm (America/New_York)"), "10-02 20:00")
        self.assertIsNone(runner.reset_time("no time here", now))


if __name__ == "__main__":
    unittest.main()
