import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "bin" / "claude-select-run"
SID = "22222222-2222-4333-8444-555555555555"
LIMIT = "You've hit your session limit · resets 6:20pm (America/New_York)"

FAKE_CLAUDE = r"""#!/bin/sh
if [ "$1" = auth ] && [ "$2" = status ]; then
  [ "$CLAUDE_CONFIG_DIR" = "$REVOKED_ACCOUNT" ] && { echo '{"loggedIn":false}'; exit 0; }
  [ "$CLAUDE_CONFIG_DIR" = "$FILE_ONLY_ACCOUNT" ] && [ "$CLAUDE_CODE_OAUTH_TOKEN" != x ] && { echo '{"loggedIn":false}'; exit 0; }
  { [ -f "$CLAUDE_CONFIG_DIR/.credentials.json" ] || [ "$CLAUDE_CONFIG_DIR" = "$KEYCHAIN_ACCOUNT" ]; } && { echo '{"loggedIn":true}'; exit 0; }
  echo '{"loggedIn":false}'; exit 0
fi
[ "$CLAUDE_CONFIG_DIR" = "$FILE_ONLY_ACCOUNT" ] && [ "$CLAUDE_CODE_OAUTH_TOKEN" != x ] && exit 1
echo "claude $CLAUDE_CONFIG_DIR" >> "$CALLS"
trap 'echo "claude got TERM" >> "$CALLS"; [ -n "$idle" ] && kill "$idle"; exit 0' TERM
sid=""; while [ $# -gt 0 ]; do case "$1" in --session-id|--resume) sid="$2";; esac; shift; done
dir="$CLAUDE_CONFIG_DIR/projects/$(pwd | sed 's/[^A-Za-z0-9]/-/g')"; mkdir -p "$dir"
if [ "$CLAUDE_CONFIG_DIR" = "$LOGIN_FAILURE" ]; then
  sleep 1
  ts=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  printf '{"type":"assistant","timestamp":"%s","message":{"content":[{"type":"text","text":"Login expired · Please run /login"}]}}\n' "$ts" >> "$dir/$sid.jsonl"
  [ -n "$HANG" ] && { sleep 600 >/dev/null 2>&1 & idle=$!; wait; }
fi
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
            (account / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": 1}}))
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
            "KEYCHAIN_ACCOUNT": "",
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

    def test_a_terminal_the_user_left_after_a_limit_is_not_reopened(self):
        self.run_claude("--resume", SID, purpose="terminal")
        self.assertEqual(self.calls_made(), ["claude a1"])
        self.assertGreater(self.state()["accounts"]["claude1"]["cooldown_until"], time.time())

    def test_a_terminal_left_idle_at_a_limit_is_reopened_on_the_next_account(self):
        args_log = self.tmp / "args.log"
        fake = self.tmp / "fakes" / "claude"
        fake.write_text(FAKE_CLAUDE.replace('echo "claude $CLAUDE_CONFIG_DIR" >> "$CALLS"',
                                            'echo "claude $CLAUDE_CONFIG_DIR" >> "$CALLS"; echo "$*" >> "$ARGS_LOG"'))
        self.run_claude("--resume", SID, "--permission-mode", "bypassPermissions", purpose="terminal", HANG="1", ARGS_LOG=str(args_log))
        self.assertEqual(self.calls_made(), ["claude a1", "claude got TERM", "claude a2"])
        reopened = args_log.read_text().splitlines()[1]
        self.assertTrue(reopened.startswith(f"--resume {SID} --permission-mode bypassPermissions -- Your last turn was cut off"))
        self.assertEqual(self.state()["divernote_current"], "claude2")

    def test_a_terminal_is_not_reopened_when_no_other_account_has_room(self):
        (self.accounts[1] / ".credentials.json").unlink()
        self.env["HANG"] = "1"
        proc = subprocess.Popen([str(RUNNER), str(self.tmp / "fakes" / "claude"), "--resume", SID], cwd=self.work,
                                env={**self.env, "DIVERNOTE_LAUNCH_PURPOSE": "terminal"})
        time.sleep(6)
        self.assertIsNone(proc.poll(), "the user's terminal stays open")
        proc.terminate()
        proc.wait(timeout=20)
        self.assertEqual(self.calls_made()[0], "claude a1")
        self.assertNotIn("claude a2", self.calls_made())

    def newsub(self):
        result = subprocess.run([str(RUNNER), "--newsub"], env=self.env, capture_output=True, text=True, timeout=60)
        return result.returncode, json.loads(result.stdout)

    def test_newsub_moves_to_the_next_account_without_cooling_the_old_one(self):
        self.run_claude("--session-id", SID, "--", "hi", LIMITED="")
        self.assertEqual(self.state()["divernote_current"], "claude1")
        code, result = self.newsub()
        self.assertEqual((code, result), (0, {"status": "switched", "from": "claude1", "account": "claude2"}))
        state = self.state()
        self.assertEqual(state["divernote_current"], "claude2")
        self.assertEqual(state["accounts"]["claude1"]["cooldown_until"], 0)
        # It wraps around, skipping accounts that are not logged in.
        self.assertEqual(self.newsub(), (0, {"status": "switched", "from": "claude2", "account": "claude1"}))
        self.run_claude("--resume", SID, "--", "go", LIMITED="")
        self.assertEqual(self.calls_made()[-1], "claude a1")

    def test_newsub_says_when_the_first_other_account_resets(self):
        self.run_claude("--session-id", SID, "--", "hi", LIMITED="")
        state = self.state()
        state["accounts"]["claude2"]["cooldown_until"] = time.time() + 3600
        (self.tmp / "state.json").write_text(json.dumps(state))
        code, result = self.newsub()
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "unavailable")
        self.assertRegex(result["detail"], r"^No other Claude account has room\. claude2 resets at \d\d:\d\d\.$")
        self.assertEqual(self.state()["divernote_current"], "claude1")
        (self.accounts[1] / ".credentials.json").unlink()
        state["accounts"]["claude2"]["cooldown_until"] = 0
        (self.tmp / "state.json").write_text(json.dumps(state))
        self.assertEqual(self.newsub()[1]["detail"], "No other Claude account is logged in.")

    def test_nothing_is_queued_when_no_other_account_has_room(self):
        (self.accounts[1] / ".credentials.json").unlink()
        self.run_claude("--session-id", SID, "--", "hi")
        self.assertEqual(self.calls_made(), ["claude a1"])

    def test_keychain_only_account_is_selected_after_file_backed_accounts_cool_down(self):
        self.accounts[2].mkdir()
        self.env["KEYCHAIN_ACCOUNT"] = str(self.accounts[2])
        self.run_claude("--session-id", SID, "--", "hi")
        self.env["LIMITED"] = str(self.accounts[1])
        self.run_claude("--resume", SID, "--", "go")
        self.run_claude("--resume", SID, "--", "again")
        self.assertEqual([call for call in self.calls_made() if call.startswith("claude")],
                         ["claude a1", "claude a2", "claude a3"])
        self.assertEqual(self.state()["divernote_current"], "claude3")

    def test_revoked_account_is_skipped(self):
        self.run_claude("--session-id", SID, REVOKED_ACCOUNT=str(self.accounts[0]))
        self.assertEqual(self.calls_made(), ["claude a2"])

    def test_unexpired_file_credentials_are_passed_to_the_selected_account(self):
        account = self.accounts[0]
        (account / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "x", "expiresAt": (time.time() + 3600) * 1000}}))
        self.run_claude("--session-id", SID, FILE_ONLY_ACCOUNT=str(account), LIMITED="")
        self.assertEqual(self.calls_made(), ["claude a1"])

    def test_file_session_is_refreshed_before_being_rejected(self):
        runner = load_runner()
        account = self.accounts[0]
        path = account / ".credentials.json"
        path.write_text(json.dumps({"other": "preserved", "claudeAiOauth": {
            "accessToken": "old", "refreshToken": "refresh", "expiresAt": 1,
            "scopes": ["user:inference"]}}))
        response = io.BytesIO(json.dumps({"access_token": "new", "refresh_token": "rotated",
                                         "expires_in": 3600}).encode())
        checked_envs = []
        def check_auth(env):
            checked_envs.append(dict(env))
            return len(checked_envs) == 2
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "wrong-account"}), \
                patch.object(runner, "auth_status", side_effect=check_auth), \
                patch.object(runner.urllib.request, "urlopen", return_value=response) as refresh:
            env = runner.account_env(str(account))
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "new")
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", checked_envs[0])
        request = refresh.call_args.args[0]
        self.assertEqual(request.full_url, "https://platform.claude.com/v1/oauth/token")
        self.assertEqual(json.loads(request.data)["refresh_token"], "refresh")
        saved = json.loads(path.read_text())
        self.assertEqual(saved["other"], "preserved")
        self.assertEqual(saved["claudeAiOauth"]["refreshToken"], "rotated")
        self.assertGreater(saved["claudeAiOauth"]["expiresAt"], time.time() * 1000)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.object(runner.urllib.request, "urlopen") as refresh:
            self.assertEqual(runner.file_access_token(str(account)), "new")
            refresh.assert_not_called()

    def test_failed_refresh_preserves_credentials_and_skips_account(self):
        runner = load_runner()
        path = self.accounts[0] / ".credentials.json"
        contents = json.dumps({"claudeAiOauth": {"accessToken": "old", "refreshToken": "r", "expiresAt": 1}})
        path.write_text(contents)
        with patch.object(runner, "auth_status", return_value=False), \
                patch.object(runner.urllib.request, "urlopen", side_effect=runner.urllib.error.URLError("unavailable")):
            self.assertIsNone(runner.account_env(str(self.accounts[0])))
        self.assertEqual(path.read_text(), contents)

    def test_expired_file_credentials_are_not_used_as_a_fallback(self):
        self.run_claude("--session-id", SID, FILE_ONLY_ACCOUNT=str(self.accounts[0]), LIMITED="")
        self.assertEqual(self.calls_made(), ["claude a2"])

    def test_revoked_unexpired_file_credentials_are_skipped(self):
        (self.accounts[0] / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "x", "expiresAt": (time.time() + 3600) * 1000}}))
        self.run_claude("--session-id", SID, REVOKED_ACCOUNT=str(self.accounts[0]), LIMITED="")
        self.assertEqual(self.calls_made(), ["claude a2"])

    def test_expired_login_ends_idle_run_and_queues_continue(self):
        elapsed = self.run_claude("--session-id", SID, LOGIN_FAILURE=str(self.accounts[0]), LIMITED="", HANG="1")
        self.assertLess(elapsed, 20)
        calls = self.calls_made()
        self.assertIn("an expired Claude login", calls[1])
        self.assertEqual(calls[2], "claude got TERM")
        self.run_claude("--resume", SID, LIMITED="")
        self.assertEqual(self.calls_made()[-1], "claude a2")

    def test_no_authenticated_account_fails_without_starting_claude(self):
        for account in self.accounts[:2]:
            (account / ".credentials.json").unlink()
        result = subprocess.run([str(RUNNER), str(self.tmp / "fakes" / "claude"), "--session-id", SID],
                                cwd=self.work, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No Claude account is logged in", result.stderr)
        self.assertFalse(self.calls.exists())

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
