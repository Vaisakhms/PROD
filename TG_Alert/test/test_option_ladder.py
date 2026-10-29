import datetime
import unittest

from option_ladder import (
    build_ladder,
    ladder_momentum,
    record_option_history,
    summarize_ladder,
)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
T0 = datetime.datetime(2026, 10, 30, 10, 0, tzinfo=IST)


def iso(minutes=0):
    return (T0 + datetime.timedelta(minutes=minutes)).isoformat()


def leg_builder(raw, side):
    return {
        "ltp": raw.get("ltp", 0.0),
        "ltpch": raw.get("ltpch", 0.0),
        "oi": raw.get("oi", 0.0),
        "oich": raw.get("oich", 0.0),
        "buildup": raw.get("buildup", "No change"),
        "bias": raw.get("bias", 0.0),
    }


def raw_strikes(**overrides):
    strikes = {}
    for strike in range(22800, 23250, 50):
        strikes[float(strike)] = {
            "CE": {"ltp": 100.0, "oi": 1e6, "oich": 0.0},
            "PE": {"ltp": 100.0, "oi": 1e6, "oich": 0.0},
        }
    for strike, legs in overrides.items():
        for side, values in legs.items():
            strikes[float(strike)][side].update(values)
    return strikes


def chain_for(strikes):
    return {
        str(strike): {
            side: {"oi": leg["oi"], "ltp": leg["ltp"]}
            for side, leg in legs.items()
        }
        for strike, legs in strikes.items()
    }


class LadderBuildTests(unittest.TestCase):
    def test_five_rows_ordered_top_to_bottom(self):
        rows = build_ladder(raw_strikes(), 23000.0, leg_builder)
        self.assertEqual(
            [r["label"] for r in rows],
            ["ATM+2", "ATM+1", "ATM", "ATM-1", "ATM-2"],
        )
        self.assertEqual(
            [r["strike"] for r in rows],
            [23100.0, 23050.0, 23000.0, 22950.0, 22900.0],
        )

    def test_missing_edge_strikes_are_skipped(self):
        strikes = raw_strikes()
        rows = build_ladder(strikes, 23200.0, leg_builder)
        self.assertEqual([r["label"] for r in rows], ["ATM", "ATM-1", "ATM-2"])

    def test_unknown_atm_gives_no_rows(self):
        self.assertEqual(build_ladder(raw_strikes(), 12345.0, leg_builder), [])


class SummaryTests(unittest.TestCase):
    def rows(self, ce_bias, pe_bias, ce_chg, pe_chg):
        strikes = raw_strikes()
        for legs in strikes.values():
            legs["CE"].update(bias=ce_bias, oich=ce_chg)
            legs["PE"].update(bias=pe_bias, oich=pe_chg)
        return build_ladder(strikes, 23000.0, leg_builder)

    def test_bullish_when_calls_long_puts_short_and_puts_added(self):
        result = summarize_ladder(self.rows(1.0, 1.0, 1e5, 6e5))
        self.assertEqual(result["direction"], "up")
        self.assertGreater(result["score"], 0.5)
        self.assertEqual(result["bullish_strikes"], 5)

    def test_bearish_when_puts_long_and_calls_added(self):
        result = summarize_ladder(self.rows(-1.0, -1.0, 6e5, 1e5))
        self.assertEqual(result["direction"], "down")
        self.assertEqual(result["bearish_strikes"], 5)

    def test_mixed_is_neutral(self):
        result = summarize_ladder(self.rows(1.0, -1.0, 3e5, 3e5))
        self.assertEqual(result["direction"], "flat")

    def test_support_and_resistance_strikes(self):
        strikes = raw_strikes(**{
            "23050": {"CE": {"oi": 9e6}},
            "22950": {"PE": {"oi": 8e6}},
        })
        result = summarize_ladder(build_ladder(strikes, 23000.0, leg_builder))
        self.assertEqual(result["resistance_strike"], 23050.0)
        self.assertEqual(result["support_strike"], 22950.0)

    def test_empty_rows(self):
        self.assertIsNone(summarize_ladder([]))


class HistoryTests(unittest.TestCase):
    def history_through(self, minutes_list, price_fn):
        history = []
        for minutes in minutes_list:
            strikes = raw_strikes()
            for strike, legs in strikes.items():
                legs["CE"]["ltp"] = price_fn(minutes, "CE")
                legs["PE"]["ltp"] = price_fn(minutes, "PE")
                legs["CE"]["oi"] = 1e6 + minutes * 1000
            history = record_option_history(
                history, iso(minutes), "06-11-2026", 23000.0, 50.0,
                chain_for(strikes),
            )
        return history

    def current_rows(self, minutes, price_fn):
        strikes = raw_strikes()
        for legs in strikes.values():
            legs["CE"]["ltp"] = price_fn(minutes, "CE")
            legs["PE"]["ltp"] = price_fn(minutes, "PE")
            legs["CE"]["oi"] = 1e6 + minutes * 1000
        return build_ladder(strikes, 23000.0, leg_builder)

    def test_history_keeps_only_nearby_strikes_and_prunes_old(self):
        history = self.history_through(
            [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36],
            lambda m, s: 100.0,
        )
        ages = [
            (datetime.datetime.fromisoformat(iso(36))
             - datetime.datetime.fromisoformat(e["t"])).total_seconds() / 60
            for e in history
        ]
        self.assertTrue(all(0 <= age <= 30 for age in ages))
        self.assertEqual(len(history[-1]["strikes"]), 9)

    def test_rerun_with_same_timestamp_does_not_duplicate(self):
        history = self.history_through([0], lambda m, s: 100.0)
        again = record_option_history(
            history, iso(0), "06-11-2026", 23000.0, 50.0,
            chain_for(raw_strikes()),
        )
        self.assertEqual(len(again), 1)

    def test_five_and_fifteen_minute_changes(self):
        def price(minutes, side):
            return 100.0 + minutes if side == "CE" else 100.0 - minutes

        history = self.history_through([0, 3, 6, 9, 12, 15], price)
        rows = self.current_rows(18, price)
        result = ladder_momentum(history, rows, iso(18), "06-11-2026")

        self.assertTrue(result["m5"]["available"])
        self.assertTrue(result["m15"]["available"])
        self.assertAlmostEqual(result["m5"]["actual_minutes"], 6.0)
        self.assertAlmostEqual(result["m15"]["actual_minutes"], 15.0)

        atm = rows[2]
        self.assertAlmostEqual(atm["ce"]["m15"]["ltp"]["abs"], 15.0)
        self.assertGreater(atm["ce"]["m5"]["ltp"]["pct"], 0)
        self.assertLess(atm["pe"]["m5"]["ltp"]["pct"], 0)
        self.assertGreater(atm["ce"]["m5"]["oi"]["abs"], 0)

    def test_sharp_bullish_shift_raises_alert(self):
        def price(minutes, side):
            if minutes < 12:
                return 100.0
            return 125.0 if side == "CE" else 80.0

        history = self.history_through([0, 3, 6, 9], price)
        rows = self.current_rows(12, price)
        result = ladder_momentum(history, rows, iso(12), "06-11-2026")
        self.assertEqual(result["m5"]["alert"]["direction"], "up")
        self.assertGreater(result["m5"]["score"], 0.5)

    def test_quiet_market_has_no_alert(self):
        history = self.history_through([0, 3, 6], lambda m, s: 100.0)
        rows = self.current_rows(9, lambda m, s: 100.0)
        result = ladder_momentum(history, rows, iso(9), "06-11-2026")
        self.assertIsNone(result["m5"]["alert"])

    def test_not_enough_history_is_unavailable(self):
        history = self.history_through([0, 3], lambda m, s: 100.0)
        rows = self.current_rows(4, lambda m, s: 100.0)
        result = ladder_momentum(history, rows, iso(4), "06-11-2026")
        self.assertFalse(result["m15"]["available"])

    def test_different_expiry_history_is_ignored(self):
        history = self.history_through([0, 3, 6], lambda m, s: 100.0)
        rows = self.current_rows(9, lambda m, s: 100.0)
        result = ladder_momentum(history, rows, iso(9), "13-11-2026")
        self.assertFalse(result["m5"]["available"])

    def test_strike_missing_from_old_entry_gives_no_change(self):
        history = self.history_through([0, 3, 6], lambda m, s: 100.0)
        for entry in history:
            entry["strikes"].pop("23100.0")
        rows = self.current_rows(9, lambda m, s: 100.0)
        ladder_momentum(history, rows, iso(9), "06-11-2026")
        self.assertIsNone(rows[0]["ce"]["m5"]["ltp"])


if __name__ == "__main__":
    unittest.main()
