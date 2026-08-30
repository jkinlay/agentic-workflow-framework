import unittest

from volume_bars import Tick, volume_bars


class VolumeBarTests(unittest.TestCase):
    def test_bars_close_at_threshold(self):
        ticks = [Tick(1, 10.0, 60), Tick(2, 11.0, 40), Tick(3, 12.0, 100)]
        bars = list(volume_bars(ticks, threshold=100))
        self.assertEqual([b.volume for b in bars], [100, 100])
        self.assertEqual([b.close for b in bars], [11.0, 12.0])

    def test_high_low_open_close(self):
        ticks = [Tick(1, 10.0, 30), Tick(2, 12.0, 30), Tick(3, 9.0, 40)]
        (bar,) = volume_bars(ticks, threshold=100)
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (10.0, 12.0, 9.0, 9.0))
        self.assertEqual((bar.open_ts, bar.close_ts), (1, 3))

    def test_threshold_validation(self):
        with self.assertRaises(ValueError):
            list(volume_bars([Tick(1, 10.0, 1)], threshold=0))

    def test_complete_bars_are_not_partial(self):
        (bar,) = volume_bars([Tick(1, 10.0, 100)], threshold=100)
        self.assertFalse(bar.partial)


if __name__ == "__main__":
    unittest.main()
