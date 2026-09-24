import unittest

from long3_watcher import QUARTER_SECONDS, next_quarter_epoch, scheduled_boundaries


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


if __name__ == "__main__":
    unittest.main()
