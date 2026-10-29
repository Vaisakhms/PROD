"""ATM +/- 2 strike ladder: combined read and 5/15 minute momentum.

Pure functions over plain dicts so they can be unit tested without Fyers.
A leg is {"ltp", "ltpch", "oi", "oich", "buildup", "bias"} where bias is
+1 (bullish for the index) .. -1 (bearish), already adjusted for CE/PE.
"""

import datetime

LADDER_OFFSETS = (2, 1, 0, -1, -2)
OFFSET_WEIGHT = {0: 1.0, 1: 0.75, -1: 0.75, 2: 0.5, -2: 0.5}
HISTORY_STRIKE_RADIUS = 6
HISTORY_MAX_AGE_MIN = 30
WINDOWS = (5, 15)
WINDOW_TOLERANCE_MIN = {5: 2.5, 15: 4.5}
# Average premium gap (CE % minus PE %) that counts as a sharp shift.
SUDDEN_PREMIUM_GAP_PCT = 10.0


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def offset_label(offset):
    if offset == 0:
        return "ATM"
    return "ATM%+d" % offset


def build_ladder(strikes, atm, leg_builder):
    """strikes: {strike_float: {"CE": raw, "PE": raw}}.

    leg_builder(raw, side) -> leg dict. Returns rows ordered ATM+2 .. ATM-2,
    skipping offsets whose strike does not exist.
    """
    ordered = sorted(strikes)
    if atm not in ordered:
        return []
    atm_index = ordered.index(atm)
    rows = []

    for offset in LADDER_OFFSETS:
        index = atm_index + offset
        if not 0 <= index < len(ordered):
            continue
        strike = ordered[index]
        legs = strikes[strike]
        rows.append({
            "offset": offset,
            "label": offset_label(offset),
            "strike": strike,
            "ce": leg_builder(legs.get("CE", {}), "CE"),
            "pe": leg_builder(legs.get("PE", {}), "PE"),
        })

    return rows


def _label(score):
    if score >= 0.5:
        return "Bullish", "up"
    if score >= 0.2:
        return "Mildly bullish", "up"
    if score <= -0.5:
        return "Bearish", "down"
    if score <= -0.2:
        return "Mildly bearish", "down"
    return "Neutral / range-bound", "flat"


def summarize_ladder(rows):
    """Combine the strikes into one directional read."""
    if not rows:
        return None

    weight_total = 0.0
    buildup_sum = 0.0
    bull_strikes = bear_strikes = 0
    ce_oi = pe_oi = ce_chg = pe_chg = 0.0

    for row in rows:
        weight = OFFSET_WEIGHT.get(row["offset"], 0.5)
        strike_bias = (row["ce"]["bias"] + row["pe"]["bias"]) / 2
        buildup_sum += weight * strike_bias
        weight_total += weight
        if strike_bias > 0.1:
            bull_strikes += 1
        elif strike_bias < -0.1:
            bear_strikes += 1
        ce_oi += row["ce"]["oi"]
        pe_oi += row["pe"]["oi"]
        ce_chg += row["ce"]["oich"]
        pe_chg += row["pe"]["oich"]

    buildup_score = buildup_sum / weight_total if weight_total else 0.0
    flow_base = abs(pe_chg) + abs(ce_chg)
    flow_score = clamp((pe_chg - ce_chg) / flow_base) if flow_base else 0.0
    pcr = pe_oi / ce_oi if ce_oi > 0 and pe_oi > 0 else None
    pcr_score = clamp((pcr - 1.0) / 0.4) if pcr is not None else 0.0

    score = 0.5 * buildup_score + 0.35 * flow_score + 0.15 * pcr_score
    label, direction = _label(score)

    resistance = max(rows, key=lambda r: r["ce"]["oi"])
    support = max(rows, key=lambda r: r["pe"]["oi"])

    reasons = [
        "%d of %d strikes lean bullish, %d bearish"
        % (bull_strikes, len(rows), bear_strikes),
        "OI added: puts %+.2fL vs calls %+.2fL (%s)" % (
            pe_chg / 1e5, ce_chg / 1e5,
            "puts building support" if pe_chg > ce_chg
            else "calls building resistance" if ce_chg > pe_chg
            else "balanced",
        ),
    ]
    if pcr is not None:
        reasons.append("PCR across these strikes: %.2f" % pcr)

    return {
        "score": score,
        "label": label,
        "direction": direction,
        "buildup_score": buildup_score,
        "flow_score": flow_score,
        "pcr": pcr,
        "ce_oi_change": ce_chg,
        "pe_oi_change": pe_chg,
        "bullish_strikes": bull_strikes,
        "bearish_strikes": bear_strikes,
        "resistance_strike": resistance["strike"],
        "support_strike": support["strike"],
        "reasons": reasons,
    }


# ---------------- rolling history for 5 / 15 minute changes ----------------

def _parse_time(value):
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def record_option_history(history, captured_at, expiry, atm, step, chain):
    """Append this run's nearby strikes and drop entries that are too old.

    chain: {"23000.0": {"CE": {"oi", "ltp"}, "PE": {...}}} for all strikes.
    """
    now = _parse_time(captured_at)
    if now is None or not chain or not step:
        return list(history or [])

    radius = HISTORY_STRIKE_RADIUS * step
    nearby = {}
    for key, legs in chain.items():
        try:
            strike = float(key)
        except (TypeError, ValueError):
            continue
        if abs(strike - atm) <= radius:
            nearby[key] = {
                side: {"oi": leg.get("oi"), "ltp": leg.get("ltp")}
                for side, leg in legs.items()
            }

    kept = []
    for entry in history or []:
        then = _parse_time(entry.get("t"))
        if then is None or entry.get("t") == captured_at:
            continue
        try:
            age = (now - then).total_seconds() / 60
        except TypeError:
            continue
        if 0 < age <= HISTORY_MAX_AGE_MIN:
            kept.append(entry)

    kept.append({
        "t": captured_at,
        "expiry": expiry,
        "atm": atm,
        "strikes": nearby,
    })
    return kept


def _pick_entry(history, now, expiry, window):
    best = None
    for entry in history or []:
        if entry.get("expiry") != expiry:
            continue
        then = _parse_time(entry.get("t"))
        if then is None:
            continue
        try:
            age = (now - then).total_seconds() / 60
        except TypeError:
            continue
        gap = abs(age - window)
        if age > 0 and gap <= WINDOW_TOLERANCE_MIN[window]:
            if best is None or gap < best[0]:
                best = (gap, age, entry)
    return (best[1], best[2]) if best else (None, None)


def _change(current, previous):
    if current is None or previous is None:
        return None
    pct = (current / previous - 1) * 100 if previous else None
    return {"abs": current - previous, "pct": pct}


def ladder_momentum(history, rows, captured_at, expiry):
    """Add m5 / m15 change dicts to each leg and return window summaries."""
    now = _parse_time(captured_at)
    summaries = {}

    for window in WINDOWS:
        key = "m%d" % window
        age, entry = (
            _pick_entry(history, now, expiry, window) if now else (None, None)
        )

        if entry is None:
            summaries[key] = {
                "minutes": window, "available": False, "actual_minutes": None,
            }
            continue

        ce_pcts, pe_pcts = [], []
        ce_oi_added = pe_oi_added = 0.0

        for row in rows:
            past = entry["strikes"].get(str(row["strike"]), {})
            for side in ("ce", "pe"):
                leg = row[side]
                before = past.get(side.upper(), {})
                price = _change(leg["ltp"], before.get("ltp"))
                oi = _change(leg["oi"], before.get("oi"))
                leg[key] = {"ltp": price, "oi": oi}
                if price and price["pct"] is not None:
                    (ce_pcts if side == "ce" else pe_pcts).append(price["pct"])
                if oi:
                    if side == "ce":
                        ce_oi_added += oi["abs"]
                    else:
                        pe_oi_added += oi["abs"]

        ce_avg = sum(ce_pcts) / len(ce_pcts) if ce_pcts else None
        pe_avg = sum(pe_pcts) / len(pe_pcts) if pe_pcts else None
        gap = ce_avg - pe_avg if ce_avg is not None and pe_avg is not None else None
        score = clamp(gap / (2 * SUDDEN_PREMIUM_GAP_PCT)) if gap is not None else None

        alert = None
        if gap is not None and abs(gap) >= SUDDEN_PREMIUM_GAP_PCT:
            alert = {
                "direction": "up" if gap > 0 else "down",
                "text": (
                    "Sharp %d-min %s shift: call premiums %+.1f%% vs put "
                    "premiums %+.1f%% (avg of 5 strikes)"
                    % (
                        window,
                        "bullish" if gap > 0 else "bearish",
                        ce_avg, pe_avg,
                    )
                ),
            }

        summaries[key] = {
            "minutes": window,
            "available": True,
            "actual_minutes": round(age, 1),
            "ce_premium_pct": ce_avg,
            "pe_premium_pct": pe_avg,
            "ce_oi_added": ce_oi_added,
            "pe_oi_added": pe_oi_added,
            "score": score,
            "alert": alert,
        }

    return summaries
