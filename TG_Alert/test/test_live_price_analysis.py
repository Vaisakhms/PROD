import datetime
import unittest
from zoneinfo import ZoneInfo

import pandas as pd

from notify import (
    analyze_series,
    completed_history,
    history_with_active_quote,
)


IST = ZoneInfo("Asia/Kolkata")


class LivePriceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.datetime(2026, 10, 27, 9, 15, tzinfo=IST)
        self.index = pd.DatetimeIndex(
            [self.start + datetime.timedelta(minutes=3 * i) for i in range(3)]
        )
        self.fetched = pd.DataFrame(
            {
                "Open": [100.0, 100.5, 101.0],
                "High": [100.5, 101.0, 101.5],
                "Low": [99.5, 100.0, 100.5],
                "Close": [100.2, 100.8, 101.2],
                "Volume": [10, 20, 30],
            },
            index=self.index,
        )

    def test_active_candle_uses_quote_for_close_and_extends_range(self):
        captured_at = self.start + datetime.timedelta(minutes=7)
        completed = self.fetched.iloc[:2]

        result = history_with_active_quote(
            completed,
            self.fetched,
            "3m",
            captured_at,
            102.0,
        )

        self.assertEqual(len(result), 3)
        self.assertEqual(result.iloc[-1]["Open"], 101.0)
        self.assertEqual(result.iloc[-1]["High"], 102.0)
        self.assertEqual(result.iloc[-1]["Low"], 100.5)
        self.assertEqual(result.iloc[-1]["Close"], 102.0)
        self.assertEqual(result.iloc[-1]["Volume"], 30)

    def test_active_candle_is_kept_when_no_bar_has_closed_yet(self):
        captured_at = self.start + datetime.timedelta(minutes=1)

        result = history_with_active_quote(
            self.fetched.iloc[:0],
            self.fetched,
            "3m",
            captured_at,
            99.0,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["Close"], 99.0)
        self.assertEqual(result.iloc[0]["Low"], 99.0)

    def test_completed_candle_is_not_added_as_an_active_candle(self):
        captured_at = self.start + datetime.timedelta(minutes=9)
        completed = self.fetched.copy()

        result = history_with_active_quote(
            completed,
            self.fetched,
            "3m",
            captured_at,
            102.0,
        )

        self.assertEqual(len(result), len(completed))
        self.assertEqual(result.iloc[-1]["Close"], 101.2)

    def test_hourly_active_candle_is_not_marked_complete_after_one_minute(self):
        hourly = self.fetched.iloc[[0]].copy()
        captured_at = self.start + datetime.timedelta(minutes=30)

        result = completed_history(hourly, "1h", captured_at)

        self.assertTrue(result.empty)

    def test_hourly_active_quote_uses_the_sixty_minute_candle(self):
        hourly = self.fetched.iloc[[0]].copy()
        captured_at = self.start + datetime.timedelta(minutes=30)

        result = history_with_active_quote(
            hourly.iloc[:0],
            hourly,
            "1h",
            captured_at,
            102.0,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["Close"], 102.0)

    def test_analysis_uses_quote_and_calculates_change_from_last_close(self):
        closes = pd.Series([100 + value for value in range(21)])
        history = pd.DataFrame({"Close": closes})

        result = analyze_series(history, current_price=121.0)

        self.assertEqual(result["close"], 121.0)
        self.assertEqual(result["ltp"], 121.0)
        self.assertAlmostEqual(result["chg"], (121.0 / 120.0 - 1) * 100)

    def test_matching_quote_preserves_last_bar_change(self):
        closes = pd.Series([100 + value for value in range(21)])
        history = pd.DataFrame({"Close": closes})

        result = analyze_series(history, current_price=120.0)

        self.assertEqual(result["close"], 120.0)
        self.assertAlmostEqual(result["chg"], (120.0 / 119.0 - 1) * 100)


if __name__ == "__main__":
    unittest.main()
