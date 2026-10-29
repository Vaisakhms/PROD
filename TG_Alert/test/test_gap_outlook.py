import datetime
import unittest
from unittest import mock

import gap_outlook
from gap_outlook import (
    IST,
    build_gap_outlook,
    build_news_digest,
    classify_news,
    interpret_flows,
    overall_weight,
    parse_fii_dii,
    parse_gift_nifty,
    parse_nifty_snapshot,
    parse_rss,
    render_gap_outlook_lines,
    verified_publish_time,
    verify_google_stories,
)

NOW_UTC = datetime.datetime(2026, 10, 29, 16, 0, tzinfo=datetime.timezone.utc)
NOW = NOW_UTC.astimezone(IST)

GIFT_TEXT = (
    '"text": "The current price of GIFT NIFTY 50 INDEX FUTURES is '
    "23,045.0\xa0USD \u2014 it has fallen \u22120.21% in the past 24 hours. "
    'Watch more."'
)

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>ECB holds rates - Reuters</title>
<description>&lt;p&gt;Energy shock&lt;/p&gt;</description>
<link>https://x/1</link><source>Reuters</source>
<pubDate>Thu, 29 Oct 2026 20:00:00 +0530</pubDate></item>
<item><title>No date story</title></item>
</channel></rss>"""


def story(title, hours_old=2, summary=""):
    return {
        "title": title,
        "summary": summary,
        "source": "Test",
        "url": "https://example.com",
        "published": NOW_UTC - datetime.timedelta(hours=hours_old),
        "feed": "Test",
    }


def records(**changes):
    names = {
        "nikkei": "Nikkei 225", "hsi": "Hang Seng", "vix": "India VIX",
        "gold": "Gold", "silver": "Silver",
        "brent": "Brent Crude", "inr": "USD/INR", "tnx": "US 10Y Yield",
        "dxy": "Dollar Index",
    }
    return [
        {"name": names[key], "change_pct": value}
        for key, value in changes.items()
    ]


class ArticleDateTests(unittest.TestCase):
    PAGE = (
        '<meta property="article:published_time" content="2026-03-23T10:59:01+05:30">'
        '<script type="application/ld+json">{"datePublished":"2026-03-23T10:59:01+05:30",'
        '"dateModified":"2026-03-23T11:01:22+05:30"}</script>'
    )

    def test_declared_date_is_extracted(self):
        parsed = verified_publish_time(self.PAGE)
        self.assertEqual(parsed.date().isoformat(), "2026-03-23")

    def test_earliest_date_wins_over_restamped_modified_date(self):
        page = (
            '{"datePublished":"2026-03-23T10:59:01+05:30",'
            '"dateModified":"2026-10-29T20:00:00+05:30"}'
        )
        self.assertEqual(verified_publish_time(page).month, 3)

    def test_page_without_date_is_unverifiable(self):
        self.assertIsNone(verified_publish_time("<html>no dates</html>"))
        self.assertIsNone(verified_publish_time(None))

    def test_stale_story_is_dropped_and_undated_story_is_dropped(self):
        fresh = datetime.datetime(2026, 10, 29, 20, 0, tzinfo=IST)
        stale = datetime.datetime(2026, 3, 23, 11, 0, tzinfo=IST)
        dates = {"fresh": fresh, "stale": stale, "undated": None}

        def fake_verify(item):
            when = dates[item["title"].split()[0].lower()]
            return None if when is None else {**item, "published": when}

        stories = [
            story("fresh Sensex surges on RBI cut"),
            story("stale Sensex crash war panic"),
            story("undated Nifty rally on FII inflows"),
        ]
        with mock.patch.object(gap_outlook, "verify_story_date", fake_verify):
            kept = verify_google_stories(stories, NOW_UTC)

        self.assertEqual([s["title"].split()[0] for s in kept], ["fresh"])


class ClassifyNewsTests(unittest.TestCase):
    def test_war_headline_is_high_impact_and_bearish(self):
        result = classify_news("Missile attack escalates war, markets plunge")
        self.assertGreaterEqual(result["impact"], 9)
        self.assertEqual(result["direction"], -1)

    def test_ecb_energy_shock_is_bearish(self):
        result = classify_news(
            "ECB holds interest rates steady at 2.5% as energy shock "
            "threatens inflation and growth"
        )
        self.assertGreaterEqual(result["impact"], 7)
        self.assertEqual(result["direction"], -1)

    def test_food_inflation_surge_is_bearish(self):
        result = classify_news(
            "Food inflation surges to 7.74%, oil shock threatens India's growth"
        )
        self.assertEqual(result["direction"], -1)

    def test_falling_crude_is_bullish(self):
        result = classify_news("Brent crude falls as supply fears ease")
        self.assertEqual(result["direction"], 1)

    def test_sensex_rally_is_bullish(self):
        result = classify_news("Sensex jumps 800 points in a broad rally")
        self.assertEqual(result["direction"], 1)
        self.assertGreaterEqual(result["impact"], 6)

    def test_falling_oil_lifting_stocks_is_bullish(self):
        result = classify_news(
            "Sensex Jumps 1,534 Points as Falling Oil Prices Lift Indian Stocks"
        )
        self.assertEqual(result["direction"], 1)

    def test_irrelevant_story_has_no_impact(self):
        result = classify_news(
            "Toyota September sales rise as production recovers"
        )
        self.assertEqual(result["impact"], 0)

    def test_impact_never_exceeds_ten(self):
        result = classify_news(
            "War crash panic: Sensex plunge, crude spike, default fears, "
            "RBI emergency, tariffs"
        )
        self.assertLessEqual(result["impact"], 10)

    def test_fed_is_case_sensitive(self):
        self.assertEqual(classify_news("She was fed up with the delay")["impact"], 0)
        self.assertGreater(classify_news("Fed holds rates")["impact"], 0)


class NewsDigestTests(unittest.TestCase):
    def test_score_is_non_zero_for_clear_headlines(self):
        digest = build_news_digest([
            story("Sensex surges as FII inflows return"),
            story("Nifty rallies on RBI rate cut hopes"),
        ], NOW_UTC)
        self.assertGreater(digest["score"], 0.3)
        self.assertEqual(digest["stats"]["positive"], 2)

    def test_old_and_duplicate_stories_are_dropped(self):
        digest = build_news_digest([
            story("Sensex crashes as war escalates", hours_old=30),
            story("Sensex surges on RBI cut", hours_old=2),
            story("Sensex surges on RBI cut", hours_old=3),
        ], NOW_UTC)
        self.assertEqual(len(digest["items"]), 1)

    def test_top_five_sorted_by_impact(self):
        stories = [story("Nifty gains %d" % i) for i in range(8)]
        stories.append(story("War erupts, missile attack, markets crash", 1))
        digest = build_news_digest(stories, NOW_UTC)
        self.assertEqual(len(digest["top"]), 5)
        self.assertIn("War erupts", digest["top"][0]["title"])

    def test_quiet_day_gives_small_score(self):
        digest = build_news_digest(
            [story("Nifty gains after budget talk")], NOW_UTC
        )
        self.assertLess(abs(digest["score"]), 0.5)

    def test_no_stories_gives_zero_score(self):
        self.assertEqual(build_news_digest([], NOW_UTC)["score"], 0.0)


class ParserTests(unittest.TestCase):
    def test_parse_gift_nifty(self):
        parsed = parse_gift_nifty(GIFT_TEXT)
        self.assertEqual(parsed["price"], 23045.0)
        self.assertAlmostEqual(parsed["change_pct_24h"], -0.21)

    def test_parse_gift_nifty_rise(self):
        parsed = parse_gift_nifty(
            "current price of GIFT NIFTY 50 INDEX FUTURES is 23,100.5 "
            "USD it has risen 0.4% in"
        )
        self.assertAlmostEqual(parsed["change_pct_24h"], 0.4)

    def test_parse_gift_nifty_missing(self):
        self.assertIsNone(parse_gift_nifty("<html>nothing here</html>"))
        self.assertIsNone(parse_gift_nifty(None))

    def test_parse_fii_dii(self):
        parsed = parse_fii_dii([
            {"category": "DII", "date": "29-Oct-2026", "netValue": "1924.41"},
            {"category": "FII/FPI", "date": "29-Oct-2026", "netValue": "-943.6"},
        ])
        self.assertEqual(parsed["fii_net"], -943.6)
        self.assertEqual(parsed["dii_net"], 1924.41)
        self.assertIsNone(parse_fii_dii({"unexpected": 1}))

    def test_parse_nifty_snapshot(self):
        parsed = parse_nifty_snapshot({"data": [
            {"index": "NIFTY BANK", "last": 1, "previousClose": 1},
            {"index": "NIFTY 50", "last": 22936.75, "open": 22690.65,
             "previousClose": 22698.85},
        ]})
        self.assertEqual(parsed["prev_close"], 22698.85)
        self.assertIsNone(parse_nifty_snapshot(None))

    def test_parse_rss_strips_html_and_source_suffix_and_skips_undated(self):
        stories = parse_rss(RSS, "Feed")
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["title"], "ECB holds rates")
        self.assertEqual(stories[0]["summary"], "Energy shock")
        self.assertEqual(parse_rss(b"not xml"), [])


class FlowsAndMetalsTests(unittest.TestCase):
    def signal(self, outlook, key):
        return [s for s in outlook["signals"] if s["key"] == key][0]

    def outlook(self, **kwargs):
        return build_gap_outlook(now=NOW, market_open=False, **kwargs)

    def test_signal_weights_sum_to_100(self):
        self.assertEqual(sum(gap_outlook.SIGNAL_WEIGHTS.values()), 100)

    def test_fii_sell_dii_buy_is_cushioned_not_bearish(self):
        bias, text = interpret_flows(-944, 1924)
        self.assertEqual(bias, "Neutral to mildly positive")
        self.assertIn("cushioning", text)

    def test_other_flow_combinations(self):
        self.assertEqual(interpret_flows(1500, 900)[0], "Bullish")
        self.assertEqual(interpret_flows(-1500, -900)[0], "Bearish")
        self.assertEqual(interpret_flows(-2500, 800)[0], "Mildly bearish")
        self.assertEqual(interpret_flows(1800, -600)[0], "Bullish")
        self.assertEqual(interpret_flows(600, -2000)[0], "Mixed")
        self.assertEqual(interpret_flows(100, -200)[0], "Neutral")

    def test_flow_signal_carries_the_interpretation(self):
        result = self.outlook(fii_dii={"fii_net": -944.0, "dii_net": 1924.0})
        note = self.signal(result, "flows")["note"]
        self.assertTrue(note.startswith("Neutral to mildly positive"))

    def test_rising_gold_and_silver_is_mildly_bearish(self):
        result = self.outlook(global_records=records(gold=1.5, silver=3.0))
        signal = self.signal(result, "metals")
        self.assertLess(signal["score"], 0)
        self.assertIn("Gold +1.50%", signal["value"])
        self.assertEqual(signal["note"], "Risk-off demand")

    def test_falling_metals_is_mildly_bullish(self):
        result = self.outlook(global_records=records(gold=-1.5, silver=-2.0))
        self.assertGreater(self.signal(result, "metals")["score"], 0)

    def test_metals_missing_is_unavailable(self):
        result = self.outlook(global_records=records(nikkei=0.5))
        self.assertFalse(self.signal(result, "metals")["available"])


class GapOutlookTests(unittest.TestCase):
    def outlook(self, **overrides):
        args = {
            "now": NOW,
            "market_open": False,
            "nifty_last": 22936.75,
            "gift": {"price": 23045.0, "source": "t", "url": "u"},
            "us_futures": {"S&P 500 Futures": 0.4, "Nasdaq Futures": 0.6},
            "global_records": records(nikkei=0.5, hsi=0.3, vix=-3, brent=-0.5),
            "fii_dii": {"fii_net": 500.0, "dii_net": 800.0, "date": "x"},
            "news": build_news_digest(
                [story("Sensex surges as FII inflows return")], NOW_UTC
            ),
        }
        args.update(overrides)
        return build_gap_outlook(**args)

    def test_bullish_inputs_give_gap_up(self):
        result = self.outlook()
        self.assertEqual(result["direction"], "up")
        self.assertGreater(result["score"], 0.2)
        self.assertAlmostEqual(result["indicated_gap"]["pct"], 0.47, places=2)
        self.assertEqual(result["mode"], "pre_open")

    def test_live_market_ignores_gift(self):
        result = self.outlook(market_open=True)
        self.assertIsNone(result["indicated_gap"])
        self.assertEqual(result["mode"], "live")
        gift = [s for s in result["signals"] if s["key"] == "gift"][0]
        self.assertFalse(gift["available"])

    def test_insane_gift_value_is_discarded(self):
        result = self.outlook(gift={"price": 30000.0})
        self.assertIsNone(result["indicated_gap"])

    def test_missing_everything_is_unavailable_not_a_crash(self):
        result = build_gap_outlook(now=NOW, market_open=False)
        self.assertIsNone(result["score"])
        self.assertEqual(result["label"], "UNAVAILABLE")
        self.assertEqual(result["alert_level"], "none")

    def test_critical_news_raises_alert_and_weight(self):
        news = build_news_digest(
            [story("War erupts, missile attack, Sensex crash, panic selling", 1)],
            NOW_UTC,
        )
        result = self.outlook(news=news)
        self.assertEqual(result["alert_level"], "critical")
        self.assertEqual(result["potential_move"]["direction"], "down")
        self.assertEqual(result["overall_weight"], 14)

    def test_big_gift_gap_is_critical(self):
        result = self.outlook(gift={"price": 23200.0})
        self.assertEqual(result["alert_level"], "critical")
        self.assertEqual(result["potential_move"]["direction"], "up")

    def test_calm_inputs_have_no_alert(self):
        result = self.outlook(
            gift={"price": 22940.0},
            news=build_news_digest([], NOW_UTC),
            us_futures={"S&P 500 Futures": 0.1},
            global_records=records(vix=-1, brent=0.1),
        )
        self.assertEqual(result["alert_level"], "none")
        self.assertIsNone(result["potential_move"])
        self.assertEqual(overall_weight("none"), 8)

    def test_opening_gap_reported_when_live(self):
        result = self.outlook(
            market_open=True,
            snapshot={"last": 22936.75, "open": 22690.65, "prev_close": 22698.85},
        )
        self.assertAlmostEqual(result["opening_gap"]["pct"], -0.036, places=3)

    def test_telegram_lines_render(self):
        text = "\n".join(render_gap_outlook_lines(self.outlook()))
        self.assertIn("GAP-UP", text)
        self.assertIn("GIFT Nifty", text)
        self.assertEqual(render_gap_outlook_lines(None), [])


if __name__ == "__main__":
    unittest.main()
