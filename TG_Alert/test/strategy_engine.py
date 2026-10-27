"""Rule-based, signal-only Nifty ATM options strategy."""

import datetime


IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
INDEX_NAMES = ("Nifty 50", "Bank Nifty", "Nifty Midcap", "Nifty 100")
TREND_FRAMES = ("5m", "10m", "15m")
SCORE_THRESHOLD = 5.0
SUPPORT_WATCH_THRESHOLD_PCT = 0.30
LEVEL_PROXIMITY_THRESHOLD_PCT = 0.30


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _evaluation_window(now):
    local_now = now.astimezone(IST)
    return (
        local_now.weekday() < 5
        and datetime.time(9, 20) <= local_now.time() <= datetime.time(15, 15)
    )


def _sma_values(frame):
    sma = frame.get("sma") or {}
    return {
        period: _number(sma.get(period, sma.get(str(period))))
        for period in (20, 50, 100)
    }


def _trend(frame):
    if not frame:
        return "Unavailable"
    close = _number(frame.get("close"))
    sma = _sma_values(frame)
    sma20 = sma[20]
    sma50 = sma[50]
    if close is None or sma20 is None or sma50 is None:
        return "Unavailable"
    if close > sma20 > sma50:
        return "Bullish"
    if close < sma20 < sma50:
        return "Bearish"
    return "Mixed"


def _trigger(frames):
    candles = (frames.get("3m") or {}).get("candles") or []
    if len(candles) < 2:
        return "Unavailable"
    latest, previous = candles[-1], candles[-2]
    close = _number(latest.get("close"))
    sma20 = _number(latest.get("sma20"))
    prior_high = _number(previous.get("high"))
    prior_low = _number(previous.get("low"))
    if None in (close, sma20, prior_high, prior_low):
        return "Unavailable"
    if close > prior_high and close > sma20:
        return "Bullish"
    if close < prior_low and close < sma20:
        return "Bearish"
    return "None"


def _level_distance(spot, levels, side):
    eligible = []
    for level in levels:
        price = _number(level.get("price"))
        if price is None or level.get("side") != side:
            continue
        if side == "support" and price > spot:
            continue
        if side == "resistance" and price < spot:
            continue
        distance = abs(spot - price) / spot * 100 if spot > 0 else None
        if distance is not None and distance <= LEVEL_PROXIMITY_THRESHOLD_PCT:
            eligible.append((distance, level))
    return min(eligible, key=lambda item: item[0]) if eligible else (None, None)


def _snapshot_spot(index_data, analysis):
    return (
        _number(index_data.get("ltp"))
        or _number(analysis.get("ltp"))
        or _number(analysis.get("close"))
    )


def _cross_index_level_bias(indices, per_index_levels):
    by_index = {}
    for name in INDEX_NAMES:
        frames = (indices.get(name) or {}).get("frames") or {}
        frame = frames.get("15m") or {}
        analysis = frame.get("analysis") or frame
        spot = _snapshot_spot(indices.get(name) or {}, analysis)
        levels = per_index_levels.get(name) or []
        support_distance, support = (
            _level_distance(spot, levels, "support")
            if spot is not None else (None, None)
        )
        resistance_distance, resistance = (
            _level_distance(spot, levels, "resistance")
            if spot is not None else (None, None)
        )
        by_index[name] = {
            "spot": spot,
            "support": support,
            "support_distance_pct": support_distance,
            "resistance": resistance,
            "resistance_distance_pct": resistance_distance,
        }

    support_names = [
        name for name, item in by_index.items() if item["support"] is not None
    ]
    resistance_names = [
        name for name, item in by_index.items()
        if item["resistance"] is not None
    ]
    support_only_names = set(support_names) - set(resistance_names)
    resistance_only_names = set(resistance_names) - set(support_names)
    bullish = (
        "Nifty 50" in support_names
        and "Bank Nifty" in support_names
        and any(name in support_names for name in ("Nifty 100", "Nifty Midcap"))
    )
    bearish = (
        "Nifty 50" in resistance_names
        and "Bank Nifty" in resistance_names
        and any(name in resistance_names for name in ("Nifty 100", "Nifty Midcap"))
    )

    if support_only_names and resistance_only_names:
        direction = None
        reason = "Nearby support and resistance are split across the indices."
    elif bullish and bearish:
        direction = None
        reason = "Both support and resistance confluence are present."
    elif bullish:
        direction = "CE"
        reason = "Nifty 50, Bank Nifty, and at least one of Nifty 100/Midcap are near support."
    elif bearish:
        direction = "PE"
        reason = "Nifty 50, Bank Nifty, and at least one of Nifty 100/Midcap are near resistance."
    else:
        direction = None
        reason = "Required Nifty 50, Bank Nifty, and Nifty 100/Midcap level confluence is absent."

    nifty = by_index.get("Nifty 50") or {}
    level = nifty.get("support") if direction == "CE" else (
        nifty.get("resistance") if direction == "PE" else None
    )
    return {
        "direction": direction,
        "action": "ATM BUY " + direction if direction else "WAIT",
        "reason": reason,
        "score": 2 if direction else 0,
        "support_indices": support_names,
        "resistance_indices": resistance_names,
        "indices": by_index,
        "nifty_level": level,
    }


def _level_reversal(candle, direction, level):
    if not candle or not level:
        return False
    open_price = _number(candle.get("open"))
    high = _number(candle.get("high"))
    low = _number(candle.get("low"))
    close = _number(candle.get("close"))
    price = _number(level.get("price"))
    if None in (open_price, high, low, close, price) or price <= 0:
        return False
    lower_wick = min(open_price, close) - low
    upper_wick = high - max(open_price, close)
    if direction == "CE":
        return (
            low <= price * 1.003
            and close >= price
            and (close >= open_price or lower_wick > upper_wick)
        )
    return (
        high >= price * 0.997
        and close <= price
        and (close <= open_price or upper_wick > lower_wick)
    )


def _scalp_opportunity(indices, per_index_levels, options):
    bias = _cross_index_level_bias(indices, per_index_levels)
    wait = {
        "decision": "WAIT",
        "direction": None,
        "reason": bias["reason"],
        "duration_minutes": 15,
        "score": 0,
    }
    direction = bias["direction"]
    if direction is None:
        return wait

    frames = (indices.get("Nifty 50") or {}).get("frames") or {}
    candles = (frames.get("3m") or {}).get("candles") or []
    candle = candles[-1] if candles else None
    if not _level_reversal(candle, direction, bias["nifty_level"]):
        wait["reason"] = (
            "Cross-index levels align, but the latest Nifty 3m candle has not "
            "confirmed a reversal at the Nifty level."
        )
        wait["setup_direction"] = direction
        return wait

    spot = _number(options.get("spot")) if options else None
    atm = _number(options.get("atm")) if options else None
    premium = _number(
        (options.get("ce" if direction == "CE" else "pe") or {}).get("ltp")
    ) if options else None
    if (
        spot is None or atm is None or abs(atm - spot) > 25
        or premium is None or premium <= 0
    ):
        wait["reason"] = (
            "The 3m reversal is confirmed, but a valid ATM strike and matching "
            "option premium are unavailable in this snapshot."
        )
        wait["setup_direction"] = direction
        wait["reference_level"] = bias["nifty_level"]
        return wait

    candle_low = _number(candle.get("low"))
    candle_high = _number(candle.get("high"))
    confirmation_indices = (
        bias["support_indices"]
        if direction == "CE"
        else bias["resistance_indices"]
    )
    return {
        "decision": "BUY ATM " + direction,
        "direction": direction,
        "reason": (
            f"{bias['reason']} Latest Nifty 3m candle confirms the reversal."
        ),
        "duration_minutes": 15,
        "score": 1,
        "premium": premium,
        "atm": atm,
        "spot": spot,
        "reference_level": bias["nifty_level"],
        "entry_trigger": candle_high if direction == "CE" else candle_low,
        "invalidation_level": candle_low if direction == "CE" else candle_high,
        "level_confirmations": confirmation_indices,
    }


def _main_strategy_plan(indices, per_index_levels, events, market_bias, oi,
                        scalp_opportunity):
    cross_index = _cross_index_level_bias(indices, per_index_levels)
    direction = cross_index["direction"]
    if direction is None:
        return {
            "decision": "WAIT",
            "direction": None,
            "score": 0,
            "score_max": 5,
            "components": {
                "cross_index": 0,
                "weighted_market_bias": 0,
                "atm_oi": 0,
                "scalp": 0,
            },
            "reason": cross_index["reason"],
            "cross_index_bias": cross_index,
            "price_confirmed": False,
        }

    expected_event = "support hold" if direction == "CE" else "resistance rejection"
    nifty_event = events.get("Nifty 50") or {}
    price_confirmed = nifty_event.get("kind") == expected_event
    weighted = _number(market_bias)
    weighted_aligned = (
        weighted is not None
        and (weighted >= 15 if direction == "CE" else weighted <= -15)
    )
    oi_aligned = bool(
        oi
        and oi.get("verdict") == ("Bullish" if direction == "CE" else "Bearish")
        and oi.get("flow") == ("Bullish" if direction == "CE" else "Bearish")
    )
    scalp_direction = scalp_opportunity.get("direction")
    scalp_opposes = (
        scalp_direction is not None and scalp_direction != direction
    )
    components = {
        "cross_index": 2,
        "weighted_market_bias": int(weighted_aligned),
        "atm_oi": int(oi_aligned),
        "scalp": int(scalp_direction == direction),
    }
    score = sum(components.values())
    if scalp_opposes:
        reason = "The scalp setup currently points in the opposite direction."
    elif not price_confirmed:
        reason = f"Nifty 15m has not confirmed the {direction} level hold/rejection."
    elif score < 3:
        reason = (
            "Directional level and 15m confirmation are present, but fewer "
            "than 3 of 5 points are available."
        )
    else:
        reason = (
            f"Cross-index confluence and Nifty 15m confirmation pass "
            f"({score}/5 points)."
        )
    decision = "BUY ATM " + direction if (
        price_confirmed and score >= 3 and not scalp_opposes
    ) else "WAIT"

    nifty_frames = (indices.get("Nifty 50") or {}).get("frames") or {}
    candles = (nifty_frames.get("15m") or {}).get("candles") or []
    candle = candles[-1] if candles else {}
    invalidation = _number(candle.get("low" if direction == "CE" else "high"))
    target_side = "resistance" if direction == "CE" else "support"
    spot = _number(
        (indices.get("Nifty 50") or {}).get("ltp")
        or ((nifty_frames.get("15m") or {}).get("analysis") or {}).get("ltp")
        or ((nifty_frames.get("15m") or {}).get("analysis") or {}).get("close")
    )
    targets = [
        _number(level.get("price"))
        for level in per_index_levels.get("Nifty 50", [])
        if level.get("side") == target_side
        and _number(level.get("price")) is not None
        and spot is not None
        and (
            _number(level.get("price")) > spot
            if direction == "CE"
            else _number(level.get("price")) < spot
        )
    ]
    target = (
        min(targets) if direction == "CE" and targets
        else max(targets) if direction == "PE" and targets
        else None
    )
    return {
        "decision": decision,
        "direction": direction,
        "score": score,
        "score_max": 5,
        "components": components,
        "reason": reason,
        "cross_index_bias": cross_index,
        "price_confirmed": price_confirmed,
        "confirmation_event": nifty_event,
        "invalidation_level": invalidation,
        "first_target": target,
        "snapshot_based": True,
    }


def _near(price, level, percent):
    return price is not None and level is not None and level != 0 and (
        abs(price - level) / abs(level) * 100 <= percent
    )


def _event(candles, levels, near_percent):
    if not candles:
        return None
    candle = candles[-1]
    close = _number(candle.get("close"))
    high = _number(candle.get("high"))
    low = _number(candle.get("low"))
    open_price = _number(candle.get("open"))
    if None in (close, high, low, open_price):
        return None
    lower_wick = min(open_price, close) - low
    upper_wick = high - max(open_price, close)
    for level in levels:
        value = _number(level.get("price"))
        if value is None:
            continue
        if level.get("side") == "support":
            if close < value * 0.999:
                return {"direction": "PE", "kind": "support breakdown",
                        "level": level}
            if _near(close, value, near_percent) and low <= value * (
                1 + near_percent / 100
            ) and close >= value and (
                close >= open_price or lower_wick > upper_wick
            ):
                return {"direction": "CE", "kind": "support hold",
                        "level": level}
        else:
            if close > value * 1.001:
                return {"direction": "CE", "kind": "resistance breakout",
                        "level": level}
            if _near(close, value, near_percent) and high >= value * (
                1 - near_percent / 100
            ) and close <= value and (
                close <= open_price or upper_wick > lower_wick
            ):
                return {"direction": "PE", "kind": "resistance rejection",
                        "level": level}
    return None


def _support_watch(indices, per_index_levels, events):
    index_states = []
    for name in INDEX_NAMES:
        index_data = indices.get(name) or {}
        frame = (index_data.get("frames") or {}).get("15m") or {}
        analysis = frame.get("analysis") or frame
        spot = _number(analysis.get("close"))
        supports = [
            level for level in per_index_levels.get(name, [])
            if level.get("side") == "support"
            and _number(level.get("price")) is not None
            and _number(level.get("price")) <= spot
        ] if spot is not None else []
        nearest = max(
            supports,
            key=lambda level: _number(level.get("price")),
            default=None,
        )
        event = events.get(name) or {}
        event_level = event.get("level") or {}
        event_price = _number(event_level.get("price"))
        distance = (
            abs(spot - _number(nearest["price"])) / spot * 100
            if nearest and spot and spot > 0 else None
        )
        event_distance = (
            abs(spot - event_price) / spot * 100
            if event_price is not None and spot and spot > 0 else None
        )

        if spot is None or not per_index_levels.get(name):
            status = "UNAVAILABLE"
        elif (
            event.get("kind") == "support breakdown"
            and event_distance is not None
            and event_distance <= SUPPORT_WATCH_THRESHOLD_PCT + 0.20
        ):
            status = "BROKEN"
        elif (
            event.get("kind") == "support hold"
            and event_distance is not None
            and event_distance <= SUPPORT_WATCH_THRESHOLD_PCT
        ):
            status = "HOLDING"
        elif distance is not None and distance <= SUPPORT_WATCH_THRESHOLD_PCT:
            status = "NEAR"
        else:
            status = "CLEAR"

        index_states.append({
            "name": name,
            "status": status,
            "support_name": nearest.get("name") if nearest else (
                event_level.get("name") if status in ("BROKEN", "HOLDING") else None
            ),
            "support_price": _number(nearest.get("price")) if nearest else (
                event_price if status in ("BROKEN", "HOLDING") else None
            ),
            "distance_pct": round(distance, 3) if distance is not None else (
                round(event_distance, 3) if event_distance is not None else None
            ),
        })

    near_indices = [
        item["name"] for item in index_states
        if item["status"] in ("NEAR", "HOLDING")
    ]
    nifty_status = index_states[0]["status"]
    if nifty_status == "BROKEN":
        status = "INVALIDATED"
    elif nifty_status == "UNAVAILABLE" or any(
        item["status"] == "UNAVAILABLE" for item in index_states
    ):
        status = "UNAVAILABLE"
    elif nifty_status in ("NEAR", "HOLDING") and len(near_indices) >= 2:
        status = "SUPPORT WATCH"
    else:
        status = "NO CONFLUENCE"

    return {
        "status": status,
        "active": status == "SUPPORT WATCH",
        "near_count": len(near_indices),
        "required_count": 2,
        "threshold_pct": SUPPORT_WATCH_THRESHOLD_PCT,
        "nifty_status": nifty_status,
        "near_indices": near_indices,
        "indices": index_states,
        "reason": (
            "Nifty and at least one other index are near/holding their own "
            "support. This is a watch state, not an entry signal."
            if status == "SUPPORT WATCH"
            else "Nifty support is broken; the bullish support thesis is invalidated."
            if status == "INVALIDATED"
            else "Waiting for Nifty and another index to approach their own support."
            if status == "NO CONFLUENCE"
            else "Support proximity cannot be assessed from the available index data."
        ),
    }


def _event_confirms_level(event, kind, level):
    if not event or event.get("kind") != kind:
        return False
    event_level = event.get("level") or {}
    event_price = _number(event_level.get("price"))
    level_price = _number(level.get("price"))
    return (
        event_level.get("name") == level.get("name")
        and event_price is not None
        and level_price is not None
        and abs(event_price - level_price) <= max(abs(level_price), 1) * 1e-9
    )


def _level_veto(direction, price, nearest_resistance, nearest_support,
                nifty_event):
    if direction == "CE":
        if nearest_resistance is None:
            return True
        level_price = _number(nearest_resistance.get("price"))
        if price is None or level_price is None:
            return True
        near_unbroken_resistance = (
            price < level_price
            and _near(price, level_price, 0.2)
            and not _event_confirms_level(
                nifty_event, "resistance breakout", nearest_resistance
            )
        )
        return near_unbroken_resistance
    if direction == "PE":
        if nearest_support is None:
            return True
        level_price = _number(nearest_support.get("price"))
        if price is None or level_price is None:
            return True
        near_unbroken_support = (
            price > level_price
            and _near(price, level_price, 0.2)
            and not _event_confirms_level(
                nifty_event, "support breakdown", nearest_support
            )
        )
        return near_unbroken_support
    return False


def _consolidating(frames, bank=False):
    candles = (frames.get("15m") or {}).get("candles") or []
    frame = frames.get("15m") or {}
    analysis = frame.get("analysis") or frame
    if len(candles) < 8:
        return False
    close = _number(analysis.get("close"))
    sma20 = _sma_values(analysis)[20]
    if close is None or sma20 is None or close == 0:
        return False
    window = candles[-8:]
    highs = [_number(item.get("high")) for item in window]
    lows = [_number(item.get("low")) for item in window]
    if any(value is None for value in highs + lows):
        return False
    range_limit = 0.5 if bank else 0.35
    return sma20 != 0 and (
        (max(highs) - min(lows)) / abs(close) * 100 <= range_limit
        and abs(close - sma20) / abs(sma20) * 100 <= 0.15
    )


def _classify(oi_change, premium_change):
    if oi_change > 0 and premium_change > 0:
        return "Long buildup"
    if oi_change > 0 and premium_change < 0:
        return "Short buildup"
    if oi_change < 0 and premium_change > 0:
        return "Short covering"
    if oi_change < 0 and premium_change < 0:
        return "Long unwinding"
    return "No change"


def _leg_bias(buildup, option_type):
    bullish = (
        ("Long buildup", "Short covering")
        if option_type == "CE"
        else ("Short buildup", "Long unwinding")
    )
    bearish = (
        ("Short buildup", "Long unwinding")
        if option_type == "CE"
        else ("Long buildup", "Short covering")
    )
    if buildup in bullish:
        return "Bullish"
    if buildup in bearish:
        return "Bearish"
    return "Neutral"


def _oi_state(options, previous, now):
    if not _evaluation_window(now):
        return None
    if not options or options.get("source") != "Fyers":
        return None
    if not previous or previous.get("expiry") != options.get("expiry"):
        return None
    try:
        previous_at = datetime.datetime.fromisoformat(previous["timestamp"])
        current_at = datetime.datetime.fromisoformat(options["captured_at"])
        elapsed = (current_at - previous_at).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    if elapsed < 150 or elapsed > 390:
        return None
    atm = _number(options.get("atm"))
    chain = options.get("chain") or {}
    prior_chain = previous.get("chain") or {}
    if atm is None:
        return None
    try:
        current_strikes = sorted(float(strike) for strike in chain)
    except (TypeError, ValueError):
        return None
    nearby_strikes = [
        strike for strike in current_strikes
        if abs(strike - atm) <= 300
    ]
    strike_steps = [
        right - left
        for left, right in zip(nearby_strikes, nearby_strikes[1:])
        if right > left
    ]
    if not strike_steps:
        return None
    strike_interval = min(strike_steps)
    max_steps = int(250 / strike_interval)
    expected_strikes = {
        atm + offset * strike_interval
        for offset in range(-max_steps, max_steps + 1)
    }
    if not expected_strikes.issubset(set(current_strikes)):
        return None
    changes = {}
    for strike, legs in chain.items():
        old_legs = prior_chain.get(strike)
        if old_legs is None:
            continue
        for option_type in ("CE", "PE"):
            current = legs.get(option_type)
            old = old_legs.get(option_type)
            if not current or not old:
                continue
            oi = _number(current.get("oi"))
            premium = _number(current.get("ltp"))
            old_oi = _number(old.get("oi"))
            old_premium = _number(old.get("ltp"))
            if None not in (oi, premium, old_oi, old_premium):
                changes[(float(strike), option_type)] = (
                    oi - old_oi, premium - old_premium
                )
    atm_strike = str(options.get("atm"))
    atm_legs = {}
    for option_type in ("CE", "PE"):
        change = changes.get((atm, option_type))
        if change is None:
            return None
        atm_legs[option_type] = {
            "oi_change": change[0],
            "premium_change": change[1],
            "buildup": _classify(change[0], change[1]),
            "bias": _leg_bias(_classify(change[0], change[1]), option_type),
        }
    for strike in expected_strikes:
        if any((strike, kind) not in changes for kind in ("CE", "PE")):
            return None
    nearby = [
        change for (strike, _), change in changes.items()
        if abs(strike - atm) <= 250
    ]
    if not nearby:
        return None
    call_added = sum(
        max(change[0], 0) for (strike, kind), change in changes.items()
        if kind == "CE" and abs(strike - atm) <= 250
    )
    put_added = sum(
        max(change[0], 0) for (strike, kind), change in changes.items()
        if kind == "PE" and abs(strike - atm) <= 250
    )
    ce_bias = atm_legs["CE"]["bias"]
    pe_bias = atm_legs["PE"]["bias"]
    verdict = (
        "Bullish" if "Bullish" in (ce_bias, pe_bias) and "Bearish" not in
        (ce_bias, pe_bias)
        else "Bearish" if "Bearish" in (ce_bias, pe_bias) and
        "Bullish" not in (ce_bias, pe_bias)
        else "Neutral"
    )
    pcr = _number(options.get("pcr"))
    if pcr is None:
        return None
    flow = (
        "Bullish" if put_added > call_added
        else "Bearish" if call_added > put_added
        else "Neutral"
    )
    return {
        "verdict": verdict, "pcr": pcr, "flow": flow,
        "call_added": call_added, "put_added": put_added,
        "legs": atm_legs, "atm_strike": atm_strike,
    }


def _make_levels(index_name, data, pivots, options):
    output = []
    index_pivots = data.get("pivots") or (
        (pivots or {}) if index_name == "Nifty 50" else {}
    )
    for key, value in index_pivots.items():
        if key == "P":
            continue
        output.append({
            "name": "Previous-day " + key,
            "price": value,
            "side": "support" if key.startswith("S") else "resistance",
            "major": False,
        })
    for name, key, side in (
        ("Previous-day high", "previous_day_high", "resistance"),
        ("Previous-day low", "previous_day_low", "support"),
        ("7-session high", "seven_day_high", "resistance"),
        ("7-session low", "seven_day_low", "support"),
    ):
        if data.get(key) is not None:
            output.append({
                "name": name, "price": data[key], "side": side,
                "major": False,
            })
    if index_name == "Nifty 50":
        if options:
            for name, key, side in (
                ("Maximum Call OI", "resistance", "resistance"),
                ("Maximum Put OI", "support", "support"),
            ):
                if options.get(key) is not None:
                    output.append({
                        "name": name, "price": options[key], "side": side,
                        "major": False,
                    })
    for timeframe in ("30m", "1h", "Daily"):
        frame = (data.get("frames") or {}).get(timeframe) or {}
        frame = frame.get("analysis") or frame
        frame15 = (data.get("frames", {}).get("15m") or {})
        frame15 = frame15.get("analysis") or frame15
        current_spot = (
            _number(data.get("ltp"))
            or _number(frame15.get("ltp"))
            or _number(frame15.get("close"))
        )
        sma = _sma_values(frame)
        for period in ("50", "100"):
            price = sma[int(period)]
            if price is not None and current_spot is not None:
                output.append({
                    "name": "SMA%s %s" % (period, timeframe),
                    "price": price,
                    "side": "support" if current_spot >= price else "resistance",
                    "major": True,
                })
    frame15 = (data.get("frames") or {}).get("15m") or {}
    frame15 = frame15.get("analysis") or frame15
    minor = _sma_values(frame15)[20]
    if minor is not None:
        close = (
            _number(data.get("ltp"))
            or _number(frame15.get("ltp"))
            or _number(frame15.get("close"))
        )
        if close is not None:
            output.append({
                "name": "SMA20 15m", "price": minor,
                "side": "support" if close >= minor else "resistance",
                "major": False,
            })
    return output


def _score_direction(score, direction, points, setups, name):
    points[direction] += score
    setups.append({"name": name, "direction": direction, "points": score})


def _all_confirm_direction(events, names, direction):
    return all(
        events.get(name) and events[name]["direction"] == direction
        for name in names
    )


def evaluate_strategy(now, indices, pivots, options, previous, state,
                      market_bias, account_size, lot_size):
    """Evaluate one cycle and return decision, details, and next state."""
    state = dict(state or {})
    today = now.date().isoformat()
    if state.get("date") != today:
        previous_position = state.get("open_position")
        state = {
            "date": today, "trades": 0, "losses": 0,
            "realized_pnl": 0.0, "open_position": previous_position,
            "last_stop_at": None,
        }
    points = {"CE": 0.0, "PE": 0.0}
    setups = []
    reasons = []
    frames = {
        name: (indices.get(name) or {}).get("frames") or {}
        for name in INDEX_NAMES
    }
    trends = {
        name: {tf: _trend((frames[name].get(tf) or {}).get("analysis"))
               for tf in TREND_FRAMES}
        for name in INDEX_NAMES
    }
    trigger = _trigger(frames.get("Nifty 50") or {})
    consolidating = {
        name: _consolidating(frames[name], name == "Bank Nifty")
        for name in INDEX_NAMES
    }
    per_index_levels = {
        name: _make_levels(name, indices.get(name) or {}, pivots, options)
        for name in INDEX_NAMES
    }
    events = {}
    for name in INDEX_NAMES:
        candles = (frames[name].get("15m") or {}).get("candles") or []
        percent = 0.2 if name == "Bank Nifty" else 0.15
        events[name] = _event(candles, per_index_levels[name], percent)
    support_watch = _support_watch(indices, per_index_levels, events)
    scalp_opportunity = _scalp_opportunity(
        indices,
        per_index_levels,
        options,
    )

    oi = _oi_state(options, previous, now)
    if oi is None:
        reasons.append("Same-interval ATM OI/premium changes unavailable or stale.")
    main_strategy = _main_strategy_plan(
        indices,
        per_index_levels,
        events,
        market_bias,
        oi,
        scalp_opportunity,
    )

    required_data_available = True
    max_age_minutes = {"3m": 6, "5m": 8, "10m": 13, "15m": 18}
    latest_bar_closes = {timeframe: [] for timeframe in max_age_minutes}
    for name in INDEX_NAMES:
        index_data = indices.get(name) or {}
        pivots_for_index = index_data.get("pivots") or (
            pivots if name == "Nifty 50" else {}
        )
        if (
            _number(index_data.get("previous_day_high")) is None
            or _number(index_data.get("previous_day_low")) is None
            or _number(index_data.get("seven_day_high")) is None
            or _number(index_data.get("seven_day_low")) is None
            or index_data.get("completed_sessions_count", 0) < 7
            or any(_number(pivots_for_index.get(level)) is None
                   for level in ("S1", "S2", "R1", "R2"))
        ):
            required_data_available = False
        for timeframe in ("3m", "5m", "10m", "15m", "30m", "1h", "Daily"):
            frame = (frames[name].get(timeframe) or {}).get("analysis") or {}
            sma = _sma_values(frame)
            if (
                _number(frame.get("close")) is None
                or _number(frame.get("rsi")) is None
                or any(sma[period] is None for period in (20, 50, 100))
            ):
                required_data_available = False
                continue
            if timeframe in max_age_minutes:
                candles = (frames[name].get(timeframe) or {}).get("candles") or []
                if not candles or _number(candles[-1].get("time")) is None:
                    required_data_available = False
                    continue
                interval = int(timeframe[:-1]) * 60
                candle_close = _number(candles[-1]["time"]) + interval
                latest_bar_closes[timeframe].append(candle_close)
                if now.timestamp() - candle_close > max_age_minutes[timeframe] * 60:
                    required_data_available = False
        candles15 = (frames[name].get("15m") or {}).get("candles") or []
        if len(candles15) < (9 if name == "Nifty 50" else 8):
            required_data_available = False
    if any(
        len(closes) != len(INDEX_NAMES)
        or max(closes) - min(closes) > 60
        for closes in latest_bar_closes.values()
    ):
        required_data_available = False
    if (
        not options
        or options.get("source") != "Fyers"
        or not options.get("spot_live")
        or not options.get("expiry_date")
        or _number(options.get("atm")) is None
        or _number(options.get("support")) is None
        or _number(options.get("resistance")) is None
        or not lot_size
    ):
        required_data_available = False
    else:
        option_spot = _number(options.get("spot"))
        atm_strike = _number(options.get("atm"))
        nifty_close = _number(
            (frames.get("Nifty 50", {}).get("15m") or {})
            .get("analysis", {}).get("close")
        )
        if (
            option_spot is None
            or nifty_close is None
            or abs(atm_strike - option_spot) > 25
            or abs(atm_strike - nifty_close) > 25
        ):
            required_data_available = False
    if not required_data_available:
        reasons.append("One or more required index/timeframe inputs are missing or stale.")

    # Matched directional setups.
    directions = ("Bullish", "Bearish")
    for trend in directions:
        direction = "CE" if trend == "Bullish" else "PE"
        if all(trends[name]["15m"] == trend for name in INDEX_NAMES):
            _score_direction(3, direction, points, setups, "A: all four 15m trends")
        if trends["Nifty 50"]["15m"] == trend and trends["Bank Nifty"]["15m"] == trend:
            _score_direction(2, direction, points, setups, "B: Nifty and Bank Nifty 15m trends")
        if consolidating["Bank Nifty"] and trends["Nifty 50"]["15m"] == trend:
            _score_direction(1, direction, points, setups, "C: Bank Nifty consolidation")
        frame15 = frames["Nifty 50"].get("15m") or {}
        bars = frame15.get("candles") or []
        if trends["Bank Nifty"]["15m"] == trend and len(bars) >= 9:
            prior_frame = dict(frame15)
            prior_frame["candles"] = bars[:-1]
            if _consolidating({"15m": prior_frame}):
                prior = bars[:-1]
                hi = max(_number(c.get("high")) or float("-inf") for c in prior[-8:])
                lo = min(_number(c.get("low")) or float("inf") for c in prior[-8:])
                close = _number(bars[-1].get("close"))
                outside = close is not None and (
                    (trend == "Bullish" and close > hi) or
                    (trend == "Bearish" and close < lo)
                )
                if outside:
                    _score_direction(1, direction, points, setups, "D: Nifty consolidation breakout")
    confirmed = {name: event for name, event in events.items() if event}
    for direction in ("CE", "PE"):
        if _all_confirm_direction(confirmed, INDEX_NAMES, direction):
            _score_direction(2, direction, points, setups, "E: all four indices confirm a level")
        nifty_event = events.get("Nifty 50")
        if (
            _all_confirm_direction(
                events, ("Nifty 50", "Bank Nifty"), direction
            )
        ):
            _score_direction(3, direction, points, setups, "F: Nifty and Bank Nifty confirm a level")
        for name in ("Nifty 50", "Bank Nifty"):
            event = events.get(name)
            if event and event["direction"] == direction and event["level"].get("major"):
                _score_direction(1.5, direction, points, setups, "G: major SMA level confirmation")
                break
    if oi:
        if oi["verdict"] == "Bullish":
            points["CE"] += 2
        elif oi["verdict"] == "Bearish":
            points["PE"] += 2
        if oi["pcr"] >= 1.2:
            points["CE"] += 1
        elif oi["pcr"] < 0.6:
            points["PE"] += 1
        if oi["flow"] == "Bullish":
            points["CE"] += 1
        elif oi["flow"] == "Bearish":
            points["PE"] += 1
    bias = _number(market_bias)
    if bias is not None and abs(bias) >= 15:
        points["CE" if bias > 0 else "PE"] += 1
    net = points["CE"] - points["PE"]
    direction = "CE" if net >= SCORE_THRESHOLD else "PE" if net <= -SCORE_THRESHOLD else None

    def confirm_level_event(wanted):
        return any(event and event["direction"] == wanted
                   for event in events.values())

    entry_filters = {
        "required_data": required_data_available,
        "trigger": direction is not None and trigger == ("Bullish" if direction == "CE" else "Bearish"),
        "5m": direction is not None and trends["Nifty 50"]["5m"] in
              (("Bullish", "Mixed") if direction == "CE" else ("Bearish", "Mixed")),
        "10m": direction is not None and trends["Nifty 50"]["10m"] in
               (("Bullish", "Mixed") if direction == "CE" else ("Bearish", "Mixed")),
        "15m": direction is not None and (
            trends["Nifty 50"]["15m"] == ("Bullish" if direction == "CE" else "Bearish")
            or confirm_level_event(direction)
        ),
        "OI": oi is not None and direction is not None and
              oi["verdict"] in (("Bullish", "Neutral") if direction == "CE" else ("Bearish", "Neutral"))
              and (oi["put_added"] >= oi["call_added"] if direction == "CE"
                   else oi["call_added"] >= oi["put_added"]),
    }
    nifty_spot = _number((frames.get("Nifty 50", {}).get("15m") or {})
                         .get("analysis", {}).get("close"))
    nifty_levels = per_index_levels.get("Nifty 50", [])
    resistance_levels = [
        x for x in nifty_levels
        if x["side"] == "resistance"
        and nifty_spot is not None and x["price"] >= nifty_spot
    ]
    support_levels = [
        x for x in nifty_levels
        if x["side"] == "support"
        and nifty_spot is not None and x["price"] <= nifty_spot
    ]
    nearest_resistance = min(
        resistance_levels,
        key=lambda x: abs(x["price"] - nifty_spot),
        default=None,
    ) if nifty_spot is not None else None
    nearest_support = min(
        support_levels,
        key=lambda x: abs(x["price"] - nifty_spot),
        default=None,
    ) if nifty_spot is not None else None
    entry_filters["level_veto"] = not _level_veto(
        direction,
        nifty_spot,
        nearest_resistance,
        nearest_support,
        events.get("Nifty 50"),
    )

    local_time = now.astimezone(IST).time()
    expiry_day = bool(options and options.get("expiry_date") == today)
    evaluation_window = _evaluation_window(now)
    entry_window = (
        evaluation_window
        and datetime.time(9, 30) <= local_time <= datetime.time(14, 45)
        and (not expiry_day or local_time <= datetime.time(12, 0))
    )
    open_position = state.get("open_position")
    daily_gate = (
        state.get("trades", 0) < 3
        and state.get("losses", 0) < 2
        and account_size is not None and account_size > 0
        and state.get("realized_pnl", 0.0) > -account_size * 0.01
    )
    if not evaluation_window:
        reasons.append("Outside the evaluation window.")
    if not entry_window:
        reasons.append("Outside the permitted entry window.")
    if account_size is None or account_size <= 0:
        reasons.append(
            "STRATEGY_ACCOUNT_SIZE is not configured; the 1% daily loss limit cannot be enforced."
        )
    if state.get("trades", 0) >= 3:
        reasons.append("Maximum three new trades for the day reached.")
    if state.get("losses", 0) >= 2:
        reasons.append("Daily limit of two losing trades reached.")
    if (
        account_size is not None and account_size > 0
        and state.get("realized_pnl", 0.0) <= -account_size * 0.01
    ):
        reasons.append("Realized loss reached the 1% daily account limit.")
    if lot_size is None or lot_size <= 0:
        reasons.append("Current exchange lot size is unavailable.")

    decision = "WAIT"
    position_action = None
    # Tracked positions are reference/paper positions; this code does not place orders.
    if open_position:
        forced_exit_reason = None
        entered_at = open_position.get("entered_at")
        if entered_at:
            try:
                if datetime.datetime.fromisoformat(entered_at).date() < now.date():
                    forced_exit_reason = "overdue time exit"
            except ValueError:
                reasons.append("Tracked position entry timestamp is invalid.")
        if forced_exit_reason is None and (
            open_position.get("expiry_date") == today
            and local_time >= datetime.time(14, 45)
        ):
            forced_exit_reason = "expiry-day close"
        elif forced_exit_reason is None and local_time >= datetime.time(15, 0):
            forced_exit_reason = "time exit"
        if forced_exit_reason:
            premium = _number((options or {}).get(
                "ce" if open_position.get("direction") == "CE" else "pe", {}
            ).get("ltp"))
            entry = _number(open_position.get("entry_premium"))
            if premium is not None and premium > 0 and entry is not None:
                pnl = (premium - entry) * open_position.get("quantity", 0)
                state["realized_pnl"] = state.get("realized_pnl", 0.0) + pnl
                if pnl < 0:
                    state["losses"] = state.get("losses", 0) + 1
                change_pct = (premium / entry - 1) * 100
                stop_pct = _number(open_position.get("stop_pct")) or -20
                if change_pct <= stop_pct:
                    state["last_stop_at"] = now.isoformat()
            else:
                reasons.append(
                    "Forced position close recorded without a current premium; "
                    "realized P&L is unavailable."
                )
            state["open_position"] = None
            position_action = "EXIT " + open_position.get("direction", "")
            reasons.append(
                "Tracked reference position exited: "
                + forced_exit_reason + "."
            )
        else:
            premium = _number((options or {}).get(
                "ce" if open_position.get("direction") == "CE" else "pe", {}
            ).get("ltp"))
        if not forced_exit_reason and premium is not None and premium > 0:
            entry = _number(open_position.get("entry_premium"))
            if entry:
                change_pct = (premium / entry - 1) * 100
                stop_pct = _number(open_position.get("stop_pct"))
                exit_reason = None
                stop_loss_triggered = (
                    stop_pct is not None and change_pct <= stop_pct
                )
                if stop_loss_triggered:
                    exit_reason = "stop loss"
                elif change_pct >= 30:
                    exit_reason = "target"
                elif change_pct >= 20 and not open_position.get("breakeven"):
                    open_position["breakeven"] = True
                if open_position.get("breakeven") and change_pct <= 0:
                    exit_reason = "trailing stop at entry"
                if (
                    direction
                    and direction != open_position.get("direction")
                    and all(entry_filters.values())
                ):
                    exit_reason = "opposite signal"
                if exit_reason:
                    pnl = (premium - entry) * open_position.get("quantity", 0)
                    state["realized_pnl"] = state.get("realized_pnl", 0.0) + pnl
                    if pnl < 0:
                        state["losses"] = state.get("losses", 0) + 1
                    if stop_loss_triggered:
                        state["last_stop_at"] = now.isoformat()
                    state["open_position"] = None
                    position_action = "EXIT " + open_position.get("direction", "")
                    reasons.append("Tracked reference position exited: " + exit_reason + ".")
                else:
                    open_position["current_premium"] = premium
                    open_position["change_pct"] = change_pct
                    open_position["stop_premium"] = (
                        entry if open_position.get("breakeven")
                        else entry * (1 + (stop_pct or -20) / 100)
                    )
                    open_position["target_premium"] = entry * 1.3
                    state["open_position"] = open_position
                    reasons.append("A tracked reference position is open; no additional entry.")
            else:
                reasons.append("Tracked position entry premium is invalid.")
        elif not forced_exit_reason:
            reasons.append("Open position quote unavailable; position retained.")

    stopped_at = state.get("last_stop_at")
    cooldown_passed = True
    if stopped_at:
        try:
            cooldown_passed = (
                now - datetime.datetime.fromisoformat(stopped_at)
            ).total_seconds() >= 900
        except ValueError:
            cooldown_passed = False
    fresh_level = False
    if stopped_at:
        try:
            stop_time = datetime.datetime.fromisoformat(stopped_at)
            for name in INDEX_NAMES:
                bars = (frames[name].get("15m") or {}).get("candles") or []
                if not bars:
                    continue
                candle_start = _number(bars[-1].get("time"))
                if candle_start is None:
                    continue
                candle_close = datetime.datetime.fromtimestamp(
                    candle_start + 15 * 60, datetime.timezone.utc
                )
                expected_trend = (
                    "Bullish" if direction == "CE"
                    else "Bearish" if direction == "PE"
                    else None
                )
                aligned_trend = (
                    expected_trend is not None
                    and trends[name]["15m"] == expected_trend
                )
                level_event = bool(
                    events.get(name)
                    and (
                        direction is None
                        or events[name]["direction"] == direction
                    )
                )
                if candle_close > stop_time and (aligned_trend or level_event):
                    fresh_level = True
                    break
        except ValueError:
            fresh_level = False
    if stopped_at and (not cooldown_passed or not fresh_level):
        reasons.append("Post-stop 15-minute cooldown/fresh confirmation gate failed.")
        daily_gate = False

    if (
        not open_position and position_action is None and direction is not None
        and main_strategy["decision"] == "BUY ATM " + direction
        and entry_window and daily_gate and lot_size and options
        and options.get("source") == "Fyers"
        and all(entry_filters.values())
    ):
        if _oi_state(options, previous, now) is None:
            reasons.append("OI confirmation is mandatory; interval data is unavailable.")
        else:
            leg = options.get("ce" if direction == "CE" else "pe") or {}
            entry_premium = _number(leg.get("ltp"))
            if entry_premium is None or entry_premium <= 0:
                reasons.append("ATM option premium is unavailable.")
            elif stopped_at and (not cooldown_passed or not fresh_level):
                pass
            else:
                decision = "BUY ATM " + direction
                state["trades"] = state.get("trades", 0) + 1
                state["open_position"] = {
                    "direction": direction,
                    "entry_premium": entry_premium,
                    "current_premium": entry_premium,
                    "stop_premium": entry_premium * 0.8,
                    "target_premium": entry_premium * 1.3,
                    "quantity": lot_size,
                    "entered_at": now.isoformat(),
                    "expiry_date": options.get("expiry_date"),
                    "breakeven": False,
                    "stop_pct": -20,
                    "reference_only": True,
                }
                reasons.append("Signal qualifies; tracked as a reference position, not an order.")
    elif direction is None:
        reasons.append("Net score did not reach the ±5 threshold.")
    elif not all(entry_filters.values()):
        reasons.append("One or more short-move, OI, or level filters failed.")

    major_levels = [level for level in nifty_levels if level.get("major")]
    nearest = min(
        major_levels,
        key=lambda x: abs(x["price"] - nifty_spot),
        default=None,
    ) if nifty_spot is not None else None
    distance = None
    if nearest and nifty_spot is not None and nearest["price"]:
        distance = abs(nifty_spot - nearest["price"]) / nearest["price"] * 100
    detail = {
        "decision": main_strategy["decision"],
        "main_strategy": main_strategy,
        "cross_index_bias": main_strategy["cross_index_bias"],
        "legacy_entry_decision": decision,
        "scalp_opportunity": scalp_opportunity,
        "timestamp_ist": now.astimezone(IST).isoformat(),
        "ce_points": round(points["CE"], 2),
        "pe_points": round(points["PE"], 2),
        "net_score": round(net, 2),
        "matched_setups": setups,
        "trends": trends,
        "trigger_3m": trigger,
        "consolidating": consolidating,
        "level_events": events,
        "support_watch": support_watch,
        "entry_filters": entry_filters,
        "oi": oi,
        "nearest_major_level": nearest,
        "level_distance_pct": distance,
        "lot_size": lot_size,
        "position_action": position_action,
        "position": state.get("open_position"),
        "reasons": reasons,
        "expiry_day": expiry_day,
        "entry_window": entry_window,
        "execution_mode": "signal_only_reference_tracking",
    }
    state["updated_at"] = now.isoformat()
    return detail, state
