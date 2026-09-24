import subprocess
import tempfile
import unittest
from pathlib import Path

from long3_watcher import (
    QUARTER_SECONDS,
    _persist_snapshot_to_branch,
    next_quarter_epoch,
    scheduled_boundaries,
)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


class WatcherScheduleTest(unittest.TestCase):
    def test_next_boundary_is_strictly_future(self):
        self.assertEqual(next_quarter_epoch(0), QUARTER_SECONDS)
        self.assertEqual(next_quarter_epoch(QUARTER_SECONDS), 2 * QUARTER_SECONDS)
        self.assertEqual(
            next_quarter_epoch(QUARTER_SECONDS + 1),
            2 * QUARTER_SECONDS,
        )

    def test_sixteen_cycles_span_three_hours_forty_five_minutes(self):
        rows = scheduled_boundaries(1000.0, 16)
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[-1] - rows[0], 15 * QUARTER_SECONDS)
        self.assertTrue(
            all(
                b - a == QUARTER_SECONDS
                for a, b in zip(rows, rows[1:])
            )
        )

    def test_handoff_does_not_repeat_current_boundary(self):
        # A queued watcher starting four minutes after :00 waits for :15.
        now = 4 * 60
        self.assertEqual(next_quarter_epoch(now), 15 * 60)


class WatcherStatePersistenceTest(unittest.TestCase):
    def test_state_push_preserves_new_main_code_and_running_checkout(self):
        """Research may merge mid-session without hot-updating the watcher."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote = root / "remote.git"
            seed = root / "seed"
            watcher = root / "watcher"
            research = root / "research"
            verify = root / "verify"

            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                check=True,
                text=True,
                capture_output=True,
            )

            seed.mkdir()
            git(seed, "init")
            git(seed, "config", "user.name", "test")
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "checkout", "-b", "main")
            (seed / "code.txt").write_text("v1\n", encoding="utf-8")
            (seed / "state.json").write_text('{"value":"old"}\n', encoding="utf-8")
            git(seed, "add", "code.txt", "state.json")
            git(seed, "commit", "-m", "seed")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "main")
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(remote),
                    "symbolic-ref",
                    "HEAD",
                    "refs/heads/main",
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            subprocess.run(
                ["git", "clone", str(remote), str(watcher)],
                check=True,
                text=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "clone", str(remote), str(research)],
                check=True,
                text=True,
                capture_output=True,
            )
            watcher_head_before = git(watcher, "rev-parse", "HEAD").stdout.strip()

            # Simulate research branch/code reaching main while watcher v1 is alive.
            git(research, "config", "user.name", "research")
            git(research, "config", "user.email", "research@example.com")
            (research / "code.txt").write_text("v2\n", encoding="utf-8")
            git(research, "add", "code.txt")
            git(research, "commit", "-m", "research v2")
            git(research, "push", "origin", "main")

            # Running watcher accumulates forward state locally on its pinned v1.
            (watcher / "state.json").write_text(
                '{"value":"watcher-new"}\n',
                encoding="utf-8",
            )

            self.assertTrue(
                _persist_snapshot_to_branch(
                    watcher,
                    "main",
                    ("state.json",),
                    remote="origin",
                    max_attempts=2,
                )
            )

            subprocess.run(
                ["git", "clone", str(remote), str(verify)],
                check=True,
                text=True,
                capture_output=True,
            )

            # Remote main keeps the research code and receives only watcher state.
            self.assertEqual(
                (verify / "code.txt").read_text(encoding="utf-8"),
                "v2\n",
            )
            self.assertEqual(
                (verify / "state.json").read_text(encoding="utf-8"),
                '{"value":"watcher-new"}\n',
            )

            # The live watcher checkout itself never moved to v2.
            self.assertEqual(
                git(watcher, "rev-parse", "HEAD").stdout.strip(),
                watcher_head_before,
            )
            self.assertEqual(
                (watcher / "code.txt").read_text(encoding="utf-8"),
                "v1\n",
            )


if __name__ == "__main__":
    unittest.main()
