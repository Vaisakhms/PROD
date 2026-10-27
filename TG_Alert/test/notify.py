"""
Live Nifty market bias report -> Telegram + Dashboard JSON.

Features:
- Automated Fyers login using ID, PIN and TOTP.
- Nifty, Bank Nifty and Nifty Midcap technical analysis.
- SMA 20/50/100, RSI, volume and candlestick OHLC.
- Options chain, PCR, OI buildup and support/resistance.
- Global markets and news sentiment.
- Telegram report.
- JSON export for GitHub Pages dashboard.
- Historical snapshots for later comparison.

Secrets must be configured in GitHub Actions.
"""

import base64
import csv
import datetime
import email.utils
import json
import os
import re
import time
import xml.etree.ElementTree as ET

from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
import requests
import yfinance as yf

from fyers_apiv3 import fyersModel

from strategy_engine import evaluate_strategy


# ============================================================
# CONFIGURATION
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FYERS_CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
FYERS_ID = os.getenv("FYERS_ID")
FYERS_PIN = os.getenv("FYERS_PIN")
FYERS_TOTP_SECRET = os.getenv("FYERS_TOTP_SECRET")
FYERS_AUTH_CODE = os.getenv("FYERS_AUTH_CODE")

REDIRECT_URI = os.getenv(
    "FYERS_REDIRECT_URI",
    "https://trade.fyers.in/api-login/redirect-uri/index.html",
)

# Dashboard files are committed by GitHub Actions.
REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DATA = REPO_ROOT / "dashboard" / "data"
LATEST_FILE = DASHBOARD_DATA / "latest.json"
SNAPSHOT_DIR = DASHBOARD_DATA / "snapshots"
HISTORY_INDEX_FILE = DASHBOARD_DATA / "history" / "index.json"
STRATEGY_STATE_FILE = DASHBOARD_DATA / "strategy-state.json"
STRATEGY_LOG_FILE = DASHBOARD_DATA / "strategy-log.jsonl"

WEIGHTS_NOTE = "Rule-based indicators, not investment advice."

TIMEFRAMES = [
    ("3m", "3", 10),
    ("5m", "5", 10),
    ("10m", "10", 10),
    ("15m", "15", 25),
    ("30m", "30", 35),
    ("1h", "60", 60),
    ("Daily", "D", 250),
]

INDICES = {
    "Nifty 50": "NSE:NIFTY50-INDEX",
    "Bank Nifty": "NSE:NIFTYBANK-INDEX",
    "Nifty Midcap": "NSE:NIFTYMIDCAP50-INDEX",
    "Nifty 100": "NSE:NIFTY100-INDEX"
}

EQUITY_TICKERS = [
    ("Dow Jones", "^DJI"),
    ("S&P 500", "^GSPC"),
    ("Nasdaq", "^IXIC"),
    ("Nikkei 225", "^N225"),
    ("Hang Seng", "^HSI"),
    ("FTSE 100", "^FTSE"),
    ("DAX", "^GDAXI"),
]

RISK_TICKERS = [
    ("Brent Crude", "BZ=F"),
    ("Dollar Index", "DX-Y.NYB"),
    ("USD/INR", "INR=X"),
    ("US 10Y Yield", "^TNX"),
    ("India VIX", "^INDIAVIX"),
]


# ============================================================
# GENERAL HELPERS
# ============================================================

def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def b64(value):
    return base64.b64encode(str(value).encode()).decode()


def safe_md(text):
    return re.sub(r"[*_`$begin:math:display$$end:math:display$]", "", str(text or ""))


def lakh(n):
    return "%s%.1fL" % ("-" if n < 0 else "", abs(n) / 1e5)


def label_from_score(score):
    if score >= 0.6:
        return "Bullish 🟢"
    if score <= -0.6:
        return "Bearish 🔴"
    return "Neutral ⚖️"


def overall_label(total):
    if total >= 40:
        return "STRONG BULLISH 🟢🟢"
    if total >= 15:
        return "BULLISH 🟢"
    if total > -15:
        return "NEUTRAL / SIDEWAYS ⚖️"
    if total > -40:
        return "BEARISH 🔴"
    return "STRONG BEARISH 🔴🔴"


def market_status(now):
    if now.weekday() >= 5:
        return "CLOSED (weekend)"

    if datetime.time(9, 15) <= now.time() <= datetime.time(15, 30):
        return "OPEN"

    return "CLOSED (data = last session)"


def json_safe(value):
    """Convert pandas/numpy/datetime values into JSON-safe values."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    if isinstance(value, (datetime.datetime, datetime.date, pd.Timestamp)):
        if pd.isna(value):
            return None

        if isinstance(value, pd.Timestamp):
            return value.isoformat()

        return value.isoformat()

    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass

    if isinstance(value, float):
        if pd.isna(value) or value == float("inf") or value == float("-inf"):
            return None

    if pd.isna(value) if not isinstance(value, (str, bytes, dict, list, tuple)) else False:
        return None

    return value


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(
            json_safe(data),
            file,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )

    temp_path.replace(path)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets missing; skipping Telegram notification.")
        return

    url = "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN

    chunks = []
    current = ""

    for line in message.split("\n"):
        if len(current) + len(line) + 1 > 3800:
            if current.strip():
                chunks.append(current)
            current = ""

        current += line + "\n"

    if current.strip():
        chunks.append(current)

    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
        }

        try:
            resp = requests.post(url, json=payload, timeout=20)

            if resp.status_code != 200:
                print(
                    "Telegram Markdown send failed (%s); retrying plain text."
                    % resp.status_code
                )

                payload.pop("parse_mode", None)
                payload["text"] = chunk.replace("*", "").replace("`", "")

                resp = requests.post(url, json=payload, timeout=20)

            print("Telegram status:", resp.status_code)

            if resp.status_code != 200:
                print("Telegram request failed.")

        except Exception as exc:
            print("Telegram send error:", str(exc))


# ============================================================
# FYERS LOGIN
# ============================================================

def _post(url, **kwargs):
    response = requests.post(url, timeout=20, **kwargs)

    try:
        body = response.json()
    except ValueError:
        body = {}

    return response.status_code, body


def _fail(step, status, body):
    message = body.get("message") or body.get("s") or ""

    raise RuntimeError(
        "Fyers login failed at %s (HTTP %s) %s"
        % (step, status, message)
    )


def fyers_auto_login():
    """Fyers ID + PIN + TOTP -> auth_code."""

    base = "https://api-t2.fyers.in/vagator/v2"

    status, body = _post(
        base + "/send_login_otp_v2",
        json={
            "fy_id": b64(FYERS_ID),
            "app_id": "2",
        },
    )

    if status != 200 or "request_key" not in body:
        _fail("send_login_otp", status, body)

    request_key = body["request_key"]

    if 30 - (int(time.time()) % 30) < 4:
        time.sleep(5)

    status, body = _post(
        base + "/verify_otp",
        json={
            "request_key": request_key,
            "otp": pyotp.TOTP(FYERS_TOTP_SECRET).now(),
        },
    )

    if status != 200 or "request_key" not in body:
        _fail("verify_totp (check TOTP secret)", status, body)

    request_key = body["request_key"]

    status, body = _post(
        base + "/verify_pin_v2",
        json={
            "request_key": request_key,
            "identity_type": "pin",
            "identifier": b64(FYERS_PIN),
        },
    )

    bearer = (body.get("data") or {}).get("access_token")

    if status != 200 or not bearer:
        _fail("verify_pin (check PIN)", status, body)

    status, body = _post(
        "https://api-t1.fyers.in/api/v3/token",
        headers={
            "Authorization": "Bearer " + bearer,
            "Content-Type": "application/json",
        },
        json={
            "fyers_id": FYERS_ID,
            "app_id": FYERS_CLIENT_ID.split("-")[0],
            "redirect_uri": REDIRECT_URI,
            "appType": "100",
            "code_challenge": "",
            "state": "None",
            "scope": "",
            "nonce": "",
            "response_type": "code",
            "create_cookie": True,
        },
    )

    codes = parse_qs(
        urlparse(body.get("Url") or "").query
    ).get("auth_code")

    if not codes:
        _fail("get_auth_code (check Client ID / redirect URI)", status, body)

    return codes[0]


_fyers = None
_fyers_error = None


def get_fyers_client():
    global _fyers, _fyers_error

    if _fyers:
        return _fyers

    if _fyers_error:
        return None

    try:
        if not FYERS_CLIENT_ID or not FYERS_SECRET_KEY:
            raise RuntimeError(
                "FYERS_CLIENT_ID / FYERS_SECRET_KEY missing"
            )

        if FYERS_ID and FYERS_PIN and FYERS_TOTP_SECRET:
            auth_code = fyers_auto_login()

        elif FYERS_AUTH_CODE:
            auth_code = FYERS_AUTH_CODE

        else:
            raise RuntimeError("No Fyers login secrets configured")

        session = fyersModel.SessionModel(
            client_id=FYERS_CLIENT_ID,
            secret_key=FYERS_SECRET_KEY,
            redirect_uri=REDIRECT_URI,
            response_type="code",
            grant_type="authorization_code",
        )

        session.set_token(auth_code)
        response = session.generate_token()

        token = response.get("access_token")

        if not token:
            raise RuntimeError(
                "Token exchange failed: %s"
                % (response.get("message") or response.get("s"))
            )

        _fyers = fyersModel.FyersModel(
            client_id=FYERS_CLIENT_ID,
            token=token,
            log_path="",
        )

        print("Fyers login OK")
        return _fyers

    except Exception as exc:
        _fyers_error = str(exc)
        print("Fyers login failed:", _fyers_error)
        return None


# ============================================================
# MARKET DATA
# ============================================================

def deduplicate_candle_rows(candles):
    """Keep the last API candle for each timestamp."""
    unique = {}
    for candle in candles:
        unique[candle[0]] = candle
    return list(unique.values())


def fetch_index_quotes():
    """Fetch one shared LTP snapshot for all configured indices."""
    fyers = get_fyers_client()
    if not fyers:
        return {}, None

    try:
        response = fyers.quotes(data={
            "symbols": ",".join(INDICES.values()),
        })
    except Exception as exc:
        print("Index quote request failed: %s" % exc)
        return {}, None

    if not isinstance(response, dict) or response.get("s") != "ok":
        message = (
            response.get("message") or response.get("s") or "invalid response"
            if isinstance(response, dict)
            else "invalid response"
        )
        print("Index quote request failed: %s" % message)
        return {}, None

    quotes = {}
    for item in response.get("d") or []:
        symbol = item.get("n")
        values = item.get("v") or {}
        ltp = values.get("lp")
        try:
            ltp = float(ltp)
        except (TypeError, ValueError):
            print("Index quote missing a valid LTP: %s" % (symbol or "unknown"))
            continue

        if not pd.notna(ltp) or ltp <= 0:
            print("Index quote has a non-positive LTP: %s" % (symbol or "unknown"))
            continue

        name = next(
            (index_name for index_name, index_symbol in INDICES.items()
             if index_symbol == symbol),
            None,
        )
        if name is None:
            print("Ignoring quote for unconfigured index: %s" % symbol)
            continue
        quotes[name] = ltp

    missing = set(INDICES) - set(quotes)
    if missing:
        print("Index LTP unavailable for: %s" % ", ".join(sorted(missing)))
    return quotes, datetime.datetime.now(IST) if quotes else None


def fetch_history(symbol, resolution, days):
    fyers = get_fyers_client()

    if not fyers:
        return pd.DataFrame()

    now = datetime.datetime.now(IST)

    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "0",
        "range_from": str(
            int((now - datetime.timedelta(days=days)).timestamp())
        ),
        "range_to": str(int(now.timestamp())),
        "cont_flag": "1",
    }

    try:
        response = fyers.history(data=data)

    except Exception as exc:
        print("History error %s [%s]: %s" % (symbol, resolution, exc))
        return pd.DataFrame()

    candles = deduplicate_candle_rows(response.get("candles") or [])
    if response.get("s") == "ok" and candles:
        df = pd.DataFrame(
            candles,
            columns=[
                "Epoch",
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
            ],
        )

        df["Datetime"] = (
            pd.to_datetime(df["Epoch"], unit="s", utc=True)
            .dt.tz_convert(IST)
        )

        return df.set_index("Datetime").sort_index()

    print(
        "History failed %s [%s]: %s"
        % (symbol, resolution, response.get("message") or "No data")
    )

    return pd.DataFrame()


def compute_rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def add_indicators(df):
    """Return a copy of OHLC data with chart indicators."""

    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()

    close = result["Close"].astype(float)

    for period in (20, 50, 100):
        result["SMA%d" % period] = close.rolling(period).mean()

    result["RSI"] = compute_rsi(close)

    return result


def serialize_candles(df, limit=500):
    """Export OHLC candles and indicator series for chart rendering."""

    if df is None or df.empty:
        return []

    enriched = add_indicators(df).tail(limit)

    candles = []

    for index, row in enriched.iterrows():
        candles.append({
            "time": int(index.timestamp()),
            "datetime": index.isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else 0,
            "sma20": float(row["SMA20"]) if pd.notna(row["SMA20"]) else None,
            "sma50": float(row["SMA50"]) if pd.notna(row["SMA50"]) else None,
            "sma100": float(row["SMA100"]) if pd.notna(row["SMA100"]) else None,
            "rsi": float(row["RSI"]) if pd.notna(row["RSI"]) else None,
        })

    return candles


def analyze_series(df, current_price=None):
    """SMA 20/50/100 + RSI + trend score (-1..+1)."""

    if df is None or df.empty or len(df) < 21:
        return None

    close = df["Close"].astype(float)
    prev = float(close.iloc[-1])
    if current_price is not None and float(current_price) != prev:
        close = pd.concat(
            [close, pd.Series([float(current_price)])],
            ignore_index=True,
        )
    elif len(close) >= 2:
        prev = float(close.iloc[-2])

    last = float(close.iloc[-1])

    sma = {}

    for period in (20, 50, 100):
        sma[period] = (
            float(close.rolling(period).mean().iloc[-1])
            if len(close) >= period
            else None
        )

    points = 0
    max_points = 0

    for period in (20, 50, 100):
        if sma[period] is not None:
            max_points += 1
            points += int(last > sma[period])

    for first, second in ((20, 50), (50, 100)):
        if sma[first] is not None and sma[second] is not None:
            max_points += 1
            points += int(sma[first] > sma[second])

    score = (
        (points / max_points) * 2 - 1
        if max_points
        else 0.0
    )

    rsi_series = compute_rsi(close)
    rsi_value = rsi_series.iloc[-1]

    return {
        "close": last,
        "chg": (last / prev - 1) * 100 if prev else 0,
        "ltp": last,
        "sma": sma,
        "rsi": float(rsi_value) if pd.notna(rsi_value) else None,
        "score": score,
        "label": label_from_score(score),
    }


def completed_history(df, timeframe, now):
    """Exclude candles that have not fully closed at the evaluation time."""
    if df is None or df.empty:
        return pd.DataFrame()

    current_time = now.astimezone(IST)
    if timeframe == "Daily":
        return df[df.index.date < current_time.date()]

    minutes = int(timeframe[:-1]) * (
        60 if timeframe.endswith("h") else 1
    )
    return df[
        (df.index + datetime.timedelta(minutes=minutes))
        <= current_time
    ]


def history_with_active_quote(df, fetched_df, timeframe, captured_at, ltp):
    """Include the API's active candle with the captured quote, if present."""
    if fetched_df is None or fetched_df.empty:
        return df

    minutes = int(timeframe[:-1]) * (
        60 if timeframe.endswith("h") else 1
    )
    interval = datetime.timedelta(minutes=minutes)
    active = fetched_df[
        (fetched_df.index <= captured_at)
        & (fetched_df.index + interval > captured_at)
    ]
    if active.empty:
        return df

    active = active.iloc[-1:].copy()
    timestamp = active.index[-1]
    if df is None:
        df = active.iloc[0:0]
    if timestamp in df.index:
        return df

    price = float(ltp)
    active.loc[timestamp, "High"] = max(
        float(active.loc[timestamp, "High"]),
        price,
    )
    active.loc[timestamp, "Low"] = min(
        float(active.loc[timestamp, "Low"]),
        price,
    )
    active.loc[timestamp, "Close"] = price
    return pd.concat([df, active]).sort_index()


def daily_history_with_latest_session(daily_df, intraday_df, current_ltp=None):
    """Append the latest intraday session as a current daily candle."""
    if daily_df is None or daily_df.empty:
        daily_df = pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"],
            index=pd.DatetimeIndex([], tz=IST),
        )

    if intraday_df is None or intraday_df.empty:
        return daily_df

    latest_intraday_date = intraday_df.index[-1].date()
    if (
        not daily_df.empty
        and latest_intraday_date <= daily_df.index[-1].date()
    ):
        return daily_df

    session = intraday_df[intraday_df.index.date == latest_intraday_date]
    if session.empty:
        return daily_df

    close = (
        float(current_ltp)
        if current_ltp is not None
        else float(session["Close"].iloc[-1])
    )
    current_session = pd.DataFrame(
        [{
            "Open": float(session["Open"].iloc[0]),
            "High": max(float(session["High"].max()), close),
            "Low": min(float(session["Low"].min()), close),
            "Close": close,
            "Volume": float(session["Volume"].sum()),
        }],
        index=[session.index[0].normalize()],
    )
    return pd.concat([daily_df, current_session]).sort_index()


def tf_line(label, analysis):
    if not analysis:
        return "  • *%s*: data unavailable" % label

    def moving_average(period):
        value = analysis["sma"][period]

        if value is None:
            return "n/a"

        marker = "▲" if analysis["close"] > value else "▼"

        return "%s%s" % (format(value, ",.0f"), marker)

    rsi_value = analysis["rsi"]

    rsi_text = "%.0f" % rsi_value if rsi_value is not None else "n/a"

    return (
        "  • *%s*: %s (%+.2f%%) | SMA20 %s 50 %s 100 %s | "
        "RSI %s | %s"
    ) % (
        label,
        format(analysis["close"], ",.1f"),
        analysis["chg"],
        moving_average(20),
        moving_average(50),
        moving_average(100),
        rsi_text,
        analysis["label"],
    )


def seven_day(df):
    """Last seven sessions: net move, up days, range position and momentum."""

    if df is None or df.empty or len(df) < 8:
        return None

    data = df.tail(8)
    closes = data["Close"].astype(float).values

    changes = [
        (closes[i] / closes[i - 1] - 1) * 100
        for i in range(1, 8)
    ]

    net = (closes[-1] / closes[0] - 1) * 100

    high = float(data["High"].iloc[1:].max())
    low = float(data["Low"].iloc[1:].min())

    position = (
        (closes[-1] - low) / (high - low) * 100
        if high > low
        else 50.0
    )

    up_days = sum(1 for change in changes if change > 0)

    score = clamp(
        0.5 * clamp(net / 2.0)
        + 0.5 * ((up_days / 7.0) * 2 - 1)
    )

    return {
        "net": net,
        "up": up_days,
        "hi": high,
        "lo": low,
        "pos": position,
        "chg": changes,
        "score": score,
    }


def pivots_from_daily(df):
    if df is None or df.empty or len(df) < 2:
        return None

    today = datetime.datetime.now(IST).date()

    completed = df[df.index.date < today]

    row = completed.iloc[-1] if not completed.empty else df.iloc[-2]

    high = float(row["High"])
    low = float(row["Low"])
    close = float(row["Close"])

    pivot = (high + low + close) / 3

    return {
        "R2": pivot + (high - low),
        "R1": 2 * pivot - low,
        "P": pivot,
        "S1": 2 * pivot - high,
        "S2": pivot - (high - low),
    }


# ============================================================
# OPTIONS / OPEN INTEREST
# ============================================================

def classify_buildup(ltp_change, oi_change):
    if oi_change > 0 and ltp_change >= 0:
        return "Long buildup"

    if oi_change > 0 and ltp_change < 0:
        return "Short buildup"

    if oi_change < 0 and ltp_change >= 0:
        return "Short covering"

    if oi_change < 0 and ltp_change < 0:
        return "Long unwinding"

    return "No change"


BUILDUP_VALUE = {
    "CE": {
        "Long buildup": 1.0,
        "Short covering": 0.5,
        "Short buildup": -1.0,
        "Long unwinding": -0.5,
        "No change": 0.0,
    },
    "PE": {
        "Long buildup": -1.0,
        "Short covering": -0.5,
        "Short buildup": 1.0,
        "Long unwinding": 0.5,
        "No change": 0.0,
    },
}


def pcr_to_score(pcr):
    if pcr >= 1.2:
        return 1.0
    if pcr >= 1.0:
        return 0.5
    if pcr >= 0.8:
        return 0.0
    if pcr >= 0.6:
        return -0.5
    return -1.0


def parse_expiry_date(value):
    if not value:
        return None

    text = str(value).strip()
    for pattern in ("%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def fetch_fyers_nifty_lot_size():
    """Read the nearest NIFTY futures lot from Fyers' public instrument master."""
    url = "https://public.fyers.in/sym_details/NSE_FO.csv"
    now_epoch = int(datetime.datetime.now(IST).timestamp())
    nearest = None
    try:
        with requests.get(url, stream=True, timeout=15) as response:
            response.raise_for_status()
            response.encoding = "utf-8"
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                row = next(csv.reader([raw_line]))
                if len(row) < 14 or row[13].strip() != "NIFTY":
                    continue
                if not row[9].endswith("FUT"):
                    continue
                try:
                    expiry_epoch = int(row[8])
                    lot_size = int(row[3])
                except (TypeError, ValueError):
                    continue
                if expiry_epoch >= now_epoch and (
                    nearest is None or expiry_epoch < nearest[0]
                ):
                    nearest = (expiry_epoch, lot_size)
                if nearest and expiry_epoch > nearest[0]:
                    break
    except requests.RequestException as exc:
        print("Fyers instrument-master lot-size lookup failed:", exc)
        return None

    if nearest is None:
        print("Fyers instrument master contained no current NIFTY futures lot.")
        return None
    return nearest[1]


def fetch_fyers_options(spot_hint):
    fyers = get_fyers_client()

    if not fyers:
        return None

    try:
        response = fyers.optionchain(
            data={
                "symbol": "NSE:NIFTY50-INDEX",
                "strikecount": 50,
                "timestamp": "",
            }
        )

    except Exception as exc:
        print("Option chain error:", exc)
        return None

    if response.get("s") != "ok":
        print("Option chain failed:", response.get("message") or "No data")
        return None

    data = response.get("data", {})
    captured_at = datetime.datetime.now(IST)
    chain = data.get("optionsChain", [])

    spot = None

    for item in chain:
        if not item.get("option_type"):
            spot = item.get("ltp")
            break

    try:
        live_spot = float(spot) if spot not in (None, "") else None
    except (TypeError, ValueError):
        live_spot = None
    spot = float(live_spot or spot_hint or 0)

    strikes = {}

    for item in chain:
        option_type = item.get("option_type")
        strike = item.get("strike_price")

        if option_type in ("CE", "PE") and strike not in (None, -1):
            strikes.setdefault(float(strike), {})[option_type] = item

    if not strikes or not spot:
        return None

    def number(item, key):
        try:
            return float(item.get(key) or 0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    atm = min(strikes, key=lambda strike: abs(strike - spot))

    ce = strikes[atm].get("CE", {})
    pe = strikes[atm].get("PE", {})

    zone = [
        strike
        for strike in strikes
        if abs(strike - atm) <= 250
    ]

    zone_ce_change = sum(
        number(strikes[strike].get("CE", {}), "oich")
        for strike in zone
    )

    zone_pe_change = sum(
        number(strikes[strike].get("PE", {}), "oich")
        for strike in zone
    )

    zone_ce_oi = sum(
        number(strikes[strike].get("CE", {}), "oi")
        for strike in zone
    )

    zone_pe_oi = sum(
        number(strikes[strike].get("PE", {}), "oi")
        for strike in zone
    )

    all_ce = sum(
        number(value.get("CE", {}), "oi")
        for value in strikes.values()
    )

    all_pe = sum(
        number(value.get("PE", {}), "oi")
        for value in strikes.values()
    )

    total_ce = float(data.get("callOi") or all_ce)
    total_pe = float(data.get("putOi") or all_pe)

    pcr = (
        total_pe / total_ce
        if total_ce > 0 and total_pe > 0
        else None
    )

    zone_pcr = (
        zone_pe_oi / zone_ce_oi
        if zone_ce_oi > 0 and zone_pe_oi > 0
        else None
    )

    resistance = max(
        strikes,
        key=lambda strike: number(strikes[strike].get("CE", {}), "oi"),
    )

    support = max(
        strikes,
        key=lambda strike: number(strikes[strike].get("PE", {}), "oi"),
    )

    ce_buildup = classify_buildup(
        number(ce, "ltpch"),
        number(ce, "oich"),
    )

    pe_buildup = classify_buildup(
        number(pe, "ltpch"),
        number(pe, "oich"),
    )

    buildup_score = (
        BUILDUP_VALUE["CE"][ce_buildup]
        + BUILDUP_VALUE["PE"][pe_buildup]
    ) / 2

    denominator = abs(zone_pe_change) + abs(zone_ce_change)

    flow_score = (
        clamp((zone_pe_change - zone_ce_change) / denominator)
        if denominator
        else 0.0
    )

    expiry = ""
    expiry_date = None
    lot_size = None

    expiry_data = data.get("expiryData")

    if isinstance(expiry_data, list) and expiry_data:
        dated_expiries = []
        for item in expiry_data:
            value = item.get("date") or item.get("expiry")
            parsed = parse_expiry_date(value)
            if parsed and parsed >= datetime.datetime.now(IST).date():
                dated_expiries.append((parsed, str(value)))
        if dated_expiries:
            expiry_date, expiry = min(dated_expiries)
        else:
            expiry = str(
                expiry_data[0].get("date")
                or expiry_data[0].get("expiry")
                or ""
            )
            expiry_date = parse_expiry_date(expiry)

    lot_value = (
        data.get("lotSize") or data.get("lot_size")
        or data.get("lotSizeNifty")
    )
    if not lot_value:
        for item in chain:
            lot_value = (
                item.get("lot_size") or item.get("lotSize")
            )
            if lot_value:
                break
    try:
        lot_size = int(lot_value) if lot_value else None
    except (TypeError, ValueError):
        lot_size = None
    if lot_size is None:
        lot_size = fetch_fyers_nifty_lot_size()

    straddle = number(ce, "ltp") + number(pe, "ltp")

    chain_snapshot = {}

    def measured_number(item, key):
        value = item.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    for strike, legs in strikes.items():
        chain_snapshot[str(strike)] = {
            option_type: {
                "oi": measured_number(item, "oi"),
                "ltp": measured_number(item, "ltp"),
            }
            for option_type, item in legs.items()
        }

    return {
        "source": "Fyers",
        "captured_at": captured_at.isoformat(),
        "expiry": expiry,
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "spot": spot,
        "spot_live": live_spot is not None and live_spot > 0,
        "atm": atm,
        "lot_size": lot_size,
        "chain": chain_snapshot,
        "ce": ce,
        "pe": pe,
        "ce_b": ce_buildup,
        "pe_b": pe_buildup,
        "straddle": straddle,
        "move_pct": straddle / spot * 100 if spot else 0.0,
        "pcr": pcr,
        "zone_pcr": zone_pcr,
        "zone_ce_chg": zone_ce_change,
        "zone_pe_chg": zone_pe_change,
        "resistance": resistance,
        "support": support,
        "pcr_score": pcr_to_score(pcr) if pcr is not None else None,
        "oi_score": clamp(0.5 * flow_score + 0.5 * buildup_score),
    }


def fetch_nse_fallback():
    """PCR and maximum OI only. NSE may block cloud IP addresses."""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
    }

    try:
        session = requests.Session()

        session.get(
            "https://www.nseindia.com",
            headers=headers,
            timeout=10,
        )

        response = session.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers=headers,
            timeout=10,
        )

        if response.status_code != 200:
            print("NSE returned HTTP", response.status_code)
            return None

        data = response.json()

        expiry = data["records"]["expiryDates"][0]

        total_ce = 0
        total_pe = 0

        max_ce = 0
        max_pe = 0

        resistance = 0
        support = 0

        for item in data["records"]["data"]:
            if item.get("expiryDate") != expiry:
                continue

            strike = item.get("strikePrice")

            if "CE" in item:
                oi = item["CE"].get("openInterest", 0)
                total_ce += oi

                if oi > max_ce:
                    max_ce = oi
                    resistance = strike

            if "PE" in item:
                oi = item["PE"].get("openInterest", 0)
                total_pe += oi

                if oi > max_pe:
                    max_pe = oi
                    support = strike

        pcr = total_pe / total_ce if total_ce else 0.0

        return {
            "source": "NSE",
            "expiry": expiry,
            "pcr": pcr,
            "resistance": resistance,
            "support": support,
            "pcr_score": pcr_to_score(pcr),
            "oi_score": None,
        }

    except Exception as exc:
        print("NSE fallback error:", exc)
        return None


def option_number(item, key):
    try:
        return float(item.get(key) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def options_for_dashboard(options):
    if not options:
        return None

    result = {
        "source": options.get("source"),
        "captured_at": options.get("captured_at"),
        "expiry": options.get("expiry"),
        "spot": options.get("spot"),
        "atm": options.get("atm"),
        "lot_size": options.get("lot_size"),
        "expiry_date": options.get("expiry_date"),
        "straddle": options.get("straddle"),
        "move_pct": options.get("move_pct"),
        "pcr": options.get("pcr"),
        "zone_pcr": options.get("zone_pcr"),
        "zone_ce_chg": options.get("zone_ce_chg"),
        "zone_pe_chg": options.get("zone_pe_chg"),
        "resistance": options.get("resistance"),
        "support": options.get("support"),
        "pcr_score": options.get("pcr_score"),
        "oi_score": options.get("oi_score"),
    }

    for option_type in ("ce", "pe"):
        item = options.get(option_type)

        if isinstance(item, dict):
            result[option_type] = {
                "ltp": option_number(item, "ltp"),
                "ltpch": option_number(item, "ltpch"),
                "oi": option_number(item, "oi"),
                "oich": option_number(item, "oich"),
                "buildup": options.get(option_type + "_b"),
            }

    return result


def render_options(options):
    if not options:
        return ["  Option data unavailable (Fyers and NSE both failed)"]

    lines = []

    if options["source"] == "Fyers":
        ce = options["ce"]
        pe = options["pe"]

        def leg(name, item, tag):
            return (
                "  • *ATM %s %d*: ₹%.1f (%+.1f) | OI %s (Δ %s) → %s"
                % (
                    name,
                    options["atm"],
                    option_number(item, "ltp"),
                    option_number(item, "ltpch"),
                    lakh(option_number(item, "oi")),
                    lakh(option_number(item, "oich")),
                    tag,
                )
            )

        lines.append(
            "  • *Expiry*: %s | *Spot*: %s | *ATM*: %d"
            % (
                options["expiry"] or "nearest",
                format(options["spot"], ",.1f"),
                options["atm"],
            )
        )

        lines.append(leg("CE", ce, options["ce_b"]))
        lines.append(leg("PE", pe, options["pe_b"]))

        lines.append(
            "  • *Straddle premium*: ₹%.1f "
            "(market implies ±%.2f%% move)"
            % (options["straddle"], options["move_pct"])
        )

        lines.append(
            "  • *OI change near ATM (±5 strikes)*: Calls %s vs Puts %s"
            % (
                lakh(options["zone_ce_chg"]),
                lakh(options["zone_pe_chg"]),
            )
        )

        lines.append(
            "  • *PCR live*: %s total | %s near ATM"
            % (
                "%.2f" % options["pcr"] if options.get("pcr") is not None else "unavailable",
                "%.2f" % options["zone_pcr"] if options.get("zone_pcr") is not None else "unavailable",
            )
        )

    else:
        lines.append(
            "  • *Expiry*: %s (NSE fallback, no ATM premium data)"
            % options["expiry"]
        )

        lines.append("  • *PCR live*: %.2f" % options["pcr"])

    lines.append(
        "  • *Resistance (max Call OI)*: %s | "
        "*Support (max Put OI)*: %s"
        % (
            format(options["resistance"], ",.0f"),
            format(options["support"], ",.0f"),
        )
    )

    return lines


# ============================================================
# GLOBAL MARKETS
# ============================================================

def yf_change(ticker):
    try:
        closes = yf.Ticker(ticker).history(period="10d")["Close"].dropna()

        if len(closes) >= 2:
            current = float(closes.iloc[-1])
            previous = float(closes.iloc[-2])

            return current, (current / previous - 1) * 100

    except Exception as exc:
        print("yfinance error %s: %s" % (ticker, exc))

    return None, None


def global_analysis():
    lines = []
    equities = []
    risks = []

    records = []

    lines.append("*Equities*")

    for name, ticker in EQUITY_TICKERS:
        price, percentage = yf_change(ticker)

        records.append({
            "name": name,
            "ticker": ticker,
            "category": "equity",
            "price": price,
            "change_pct": percentage,
        })

        if percentage is None:
            lines.append("  • %s: unavailable" % name)

        else:
            equities.append(percentage)

            lines.append(
                "  • %s: %s (%+.2f%%)"
                % (name, format(price, ",.2f"), percentage)
            )

    lines.append("*Risk gauges* (up = negative for India)")

    for name, ticker in RISK_TICKERS:
        price, percentage = yf_change(ticker)

        records.append({
            "name": name,
            "ticker": ticker,
            "category": "risk",
            "price": price,
            "change_pct": percentage,
        })

        if percentage is None:
            lines.append("  • %s: unavailable" % name)

        else:
            risks.append(percentage)

            lines.append(
                "  • %s: %s (%+.2f%%)"
                % (name, format(price, ",.2f"), percentage)
            )

    score = None

    if equities or risks:
        equity_score = (
            clamp((sum(equities) / len(equities)) / 1.0)
            if equities
            else 0.0
        )

        risk_score = (
            -clamp((sum(risks) / len(risks)) / 1.5)
            if risks
            else 0.0
        )

        score = 0.75 * equity_score + 0.25 * risk_score

    return lines, score, records


# ============================================================
# NEWS
# ============================================================

NEWS_QUERIES = [
    'Nifty OR Sensex OR "Indian stock market"',
    'RBI OR "Federal Reserve" OR inflation OR "interest rate"',
    '"crude oil" OR war OR tariff OR sanctions stocks',
    'FII OR FPI OR "foreign investors" India',
]

POS_WORDS = [
    "rally", "rallies", "surge", "surges", "gain", "gains",
    "jumps", "soars", "record high", "rate cut", "cools",
    "eases", "inflow", "inflows", "upgrade", "beats",
    "rebound", "rebounds", "recovers", "optimism",
    "ceasefire", "bullish",
]

NEG_WORDS = [
    "fall", "falls", "plunge", "plunges", "crash", "slump",
    "slumps", "selloff", "sell-off", "drops", "slides",
    "tumbles", "rate hike", "outflow", "outflows", "selling",
    "downgrade", "fears", "recession", "war", "tariff",
    "tariffs", "sanctions", "attack", "misses", "losses",
    "warning", "bearish",
]

CONCERN_WORDS = [
    "RBI", "Fed", "Federal Reserve", "war", "tariff",
    "tariffs", "sanctions", "ceasefire", "election", "budget",
    "GDP", "CPI", "inflation", "crude", "OPEC", "FII",
    "FPI", "default", "rate cut", "rate hike",
]


def _count(words, text):
    return sum(
        1
        for word in words
        if re.search(r"\b" + re.escape(word) + r"\b", text)
    )


def fetch_news():
    items = []
    seen = set()

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for query in NEWS_QUERIES:
        url = (
            "https://news.google.com/rss/search?q=%s"
            "&hl=en-IN&gl=IN&ceid=IN:en"
            % quote_plus(query + " when:1d")
        )

        try:
            response = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )

            response.raise_for_status()

            root = ET.fromstring(response.content)

        except Exception as exc:
            print("News error:", exc)
            continue

        for item in list(root.iter("item"))[:8]:
            title = safe_md(item.findtext("title"))
            key = title.lower()[:60]

            if not title or key in seen:
                continue

            seen.add(key)

            try:
                published = email.utils.parsedate_to_datetime(
                    item.findtext("pubDate")
                )

                if published.tzinfo is None:
                    published = published.replace(
                        tzinfo=datetime.timezone.utc
                    )

                age_hours = (
                    now_utc - published.astimezone(datetime.timezone.utc)
                ).total_seconds() / 3600

            except Exception:
                age_hours = 24

            if age_hours > 30:
                continue

            low = title.lower()

            sentiment = (
                _count(POS_WORDS, low)
                - _count(NEG_WORDS, low)
            )

            tags = [
                word
                for word in CONCERN_WORDS
                if re.search(r"\b" + re.escape(word) + r"\b", title, re.I)
            ]

            link = item.findtext("link") or ""

            items.append({
                "title": title,
                "source": safe_md(item.findtext("source") or ""),
                "url": link,
                "score": (sentiment > 0) - (sentiment < 0),
                "tags": tags[:2],
                "age": max(0, age_hours),
            })

    scored = [
        item["score"]
        for item in items
        if item["score"] != 0
    ]

    score = (
        clamp(1.5 * sum(scored) / len(scored))
        if scored
        else 0.0
    )

    items.sort(
        key=lambda item: (
            not (item["score"] or item["tags"]),
            item["age"],
        )
    )

    return items[:7], score if items else None


def render_news(items):
    if not items:
        return ["  No recent headlines retrieved"]

    output = []

    for item in items:
        icon = (
            "🟢" if item["score"] > 0
            else "🔴" if item["score"] < 0
            else "⚪"
        )

        tag = (
            " [%s]" % ", ".join(item["tags"])
            if item["tags"]
            else ""
        )

        output.append(
            "  %s %s%s _(%s)_"
            % (
                icon,
                item["title"][:110],
                tag,
                item["source"],
            )
        )

    output.append(
        "  _Headline bias = keyword-based, treat as a rough guide_"
    )

    return output


# ============================================================
# DASHBOARD EXPORT
# ============================================================

def build_dashboard_payload(
    now,
    analysis,
    chart_data,
    seven,
    pivots,
    options,
    global_records,
    news_items,
    news_score,
    global_score,
    components,
    total,
    weight_sum,
    status,
    strategy,
    live_quotes=None,
    ltp_captured_at=None,
):
    index_data = {}

    for name in INDICES:
        index_data[name] = {
            "ltp": (live_quotes or {}).get(name),
            "ltp_captured_at": ltp_captured_at.isoformat()
            if ltp_captured_at and name in (live_quotes or {})
            else None,
            "timeframes": analysis.get(name, {}),
            "seven_day": seven.get(name),
            "charts": chart_data.get(name, {}),
        }

    payload = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Kolkata",
        "market_status": status,
        "ltp_captured_at": (
            ltp_captured_at.isoformat() if ltp_captured_at else None
        ),
        "overall": {
            "score": total,
            "label": overall_label(total),
            "coverage_pct": weight_sum,
        },
        "indices": index_data,
        "pivots": pivots,
        "options": options_for_dashboard(options),
        "strategy": strategy,
        "global_markets": global_records,
        "news": {
            "score": news_score,
            "items": news_items,
        },
        "scores": {
            "global_markets": global_score,
            "news_sentiment": news_score,
            "components": [
                {
                    "name": name,
                    "weight": weight,
                    "score": score,
                    "contribution": (
                        weight * score
                        if score is not None
                        else None
                    ),
                }
                for name, weight, score in components
            ],
        },
        "disclaimer": WEIGHTS_NOTE,
    }

    return json_safe(payload)


def save_dashboard_snapshot(payload):
    """
    Write latest.json and an immutable per-run snapshot.

    Historical index is sorted newest-first and limited to three entries.
    """

    DASHBOARD_DATA.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)

    write_json(LATEST_FILE, payload)

    timestamp = datetime.datetime.fromisoformat(
        payload["generated_at"]
    )

    snapshot_name = timestamp.strftime("%Y%m%d-%H%M%S") + ".json"

    snapshot_path = SNAPSHOT_DIR / snapshot_name

    write_json(snapshot_path, payload)

    try:
        if HISTORY_INDEX_FILE.exists():
            with HISTORY_INDEX_FILE.open("r", encoding="utf-8") as file:
                history = json.load(file)
        else:
            history = []

    except (json.JSONDecodeError, OSError):
        history = []

    history = [
        item for item in history
        if item.get("file") != snapshot_name
    ]

    history.insert(0, {
        "file": snapshot_name,
        "generated_at": payload["generated_at"],
        "generated_at_ist": payload["generated_at_ist"],
        "score": payload["overall"]["score"],
        "label": payload["overall"]["label"],
        "nifty_close": (
            payload["indices"]
            .get("Nifty 50", {})
            .get("timeframes", {})
            .get("15m", {})
            .get("close")
        ),
    })

    history = history[:3]

    # Remove snapshots outside the retained history window.
    retained = {item["file"] for item in history}

    for file in SNAPSHOT_DIR.glob("*.json"):
        if file.name not in retained:
            try:
                file.unlink()
            except OSError:
                pass

    write_json(HISTORY_INDEX_FILE, history)

    print("Dashboard data written:", LATEST_FILE)
    print("Snapshot written:", snapshot_path)


def read_strategy_state():
    if not STRATEGY_STATE_FILE.exists():
        return {}
    with STRATEGY_STATE_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise ValueError("Strategy state must contain a JSON object.")
    return state


def persist_strategy_state(state, decision):
    write_json(STRATEGY_STATE_FILE, state)
    with STRATEGY_LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(
            json_safe(decision),
            ensure_ascii=False,
            allow_nan=False,
        ))
        file.write("\n")


def format_strategy_report(strategy):
    oi = strategy.get("oi") or {}
    position = strategy.get("position") or {}
    major = strategy.get("nearest_major_level") or {}
    setups = strategy.get("matched_setups") or []
    level_events = [
        "%s: %s at %s"
        % (
            name,
            event["kind"],
            event["level"].get("name", "level"),
        )
        for name, event in (strategy.get("level_events") or {}).items()
        if event
    ]
    lines = [
        "\n*7. NIFTY ATM STRATEGY (signal only; no orders placed)*",
        "  • *Decision*: %s" % strategy["decision"],
        "  • *Scalp opportunity*: %s — %s"
        % (
            (strategy.get("scalp_opportunity") or {}).get("decision", "WAIT"),
            (strategy.get("scalp_opportunity") or {}).get(
                "reason", "Scalp filters not evaluated."
            ),
        ),
        "  • *Time*: %s" % strategy["timestamp_ist"],
        "  • *Current exchange lot size*: %s"
        % (strategy.get("lot_size") or "Unavailable"),
        "  • *Score*: CE %.1f | PE %.1f | net %+.1f"
        % (
            strategy["ce_points"],
            strategy["pe_points"],
            strategy["net_score"],
        ),
        "  • *Trigger/trends*: 3m %s | 5m %s | 10m %s | 15m %s"
        % (
            strategy["trigger_3m"],
            strategy["trends"]["Nifty 50"]["5m"],
            strategy["trends"]["Nifty 50"]["10m"],
            strategy["trends"]["Nifty 50"]["15m"],
        ),
        "  • *OI*: %s | PCR %s | flow %s"
        % (
            oi.get("verdict", "Unavailable"),
            "%.2f" % oi["pcr"] if oi.get("pcr") is not None else "Unavailable",
            oi.get("flow", "Unavailable"),
        ),
        "  • *Matched setups*: %s"
        % (
            "; ".join(
                "%s %s +%s"
                % (item["name"], item["direction"], item["points"])
                for item in setups
            ) or "None"
        ),
        "  • *15m level confirmations*: %s"
        % ("; ".join(level_events) or "None"),
        "  • *Nearest major level*: %s %s (%s)"
        % (
            major.get("name", "Unavailable"),
            format(major["price"], ",.2f") if major.get("price") is not None else "",
            (
                "%.2f%% away" % strategy["level_distance_pct"]
                if strategy.get("level_distance_pct") is not None
                else "distance unavailable"
            ),
        ),
        "  • *Reason*: %s" % "; ".join(strategy.get("reasons") or []),
    ]
    if strategy.get("position_action"):
        lines.append("  • *Position action*: %s" % strategy["position_action"])
    if position:
        lines.append(
            "  • *Tracked reference position (not broker-confirmed)*: %s x %s "
            "units | entry ₹%.2f | current ₹%.2f | stop ₹%.2f | "
            "target ₹%.2f | time exit 15:00 IST"
            % (
                position.get("direction"),
                position.get("quantity"),
                position.get("entry_premium", 0),
                position.get("current_premium", 0),
                position.get("stop_premium", 0),
                position.get("target_premium", 0),
            )
        )
    return lines


# ============================================================
# MAIN
# ============================================================

def main():
    now = datetime.datetime.now(IST)
    prior_strategy_state = read_strategy_state()

    status = market_status(now)

    analysis = {}
    daily_data = {}
    chart_data = {}
    intraday_data = {}
    frame_history = {}
    raw_history = {}

    for name, symbol in INDICES.items():
        analysis[name] = {}
        chart_data[name] = {}
        frame_history[name] = {}
        raw_history[name] = {}

        for label, resolution, days in TIMEFRAMES:
            fetched = fetch_history(symbol, resolution, days)
            raw_history[name][label] = fetched
            df = completed_history(fetched, label, now)
            frame_history[name][label] = df

            analysis_df = df
            chart_df = df

            if label == "3m":
                intraday_data[name] = df

            if label == "Daily":
                daily_data[name] = df
                analysis_df = daily_history_with_latest_session(
                    df,
                    intraday_data.get(name),
                )
                chart_df = analysis_df

            # Keep enough candles for charting without making JSON huge.
            chart_data[name][label] = serialize_candles(
                chart_df,
                limit=1200 if label in ("3m", "5m", "10m") else 500,
            )

            analysis[name][label] = analyze_series(analysis_df)

            time.sleep(0.15)

    live_quotes, ltp_captured_at = fetch_index_quotes()
    if ltp_captured_at:
        now = ltp_captured_at
        status = market_status(now)
        for name in INDICES:
            for label, _, _ in TIMEFRAMES:
                frame_history[name][label] = completed_history(
                    raw_history[name].get(label),
                    label,
                    ltp_captured_at,
                )
            daily_data[name] = frame_history[name]["Daily"]
            intraday_data[name] = frame_history[name]["3m"]

    for name in INDICES:
        ltp = live_quotes.get(name)
        if ltp is None:
            continue

        for label, _, _ in TIMEFRAMES:
            if label == "Daily":
                analysis_df = daily_history_with_latest_session(
                    daily_data.get(name),
                    raw_history[name].get("3m"),
                    current_ltp=ltp,
                )
                chart_data[name][label] = serialize_candles(
                    analysis_df,
                    limit=500,
                )
                has_current_session = (
                    analysis_df is not None
                    and daily_data.get(name) is not None
                    and len(analysis_df) > len(daily_data[name])
                )
                analysis[name][label] = analyze_series(
                    analysis_df
                    if has_current_session
                    else daily_data.get(name),
                    current_price=None if has_current_session else ltp,
                )
            else:
                chart_df = history_with_active_quote(
                    frame_history[name].get(label),
                    raw_history[name].get(label),
                    label,
                    ltp_captured_at,
                    ltp,
                )
                chart_data[name][label] = serialize_candles(
                    chart_df,
                    limit=1200 if label in ("3m", "5m", "10m") else 500,
                )
                analysis[name][label] = analyze_series(
                    frame_history[name].get(label),
                    current_price=ltp,
                )

            if analysis[name][label]:
                analysis[name][label]["ltp"] = ltp

    nifty = analysis["Nifty 50"]
    bank = analysis["Bank Nifty"]

    seven = {
        name: seven_day(daily_data.get(name))
        for name in INDICES
    }

    pivots = pivots_from_daily(daily_data.get("Nifty 50"))

    spot_hint = (
        nifty["15m"]["close"]
        if nifty.get("15m")
        else None
    )

    strategy_indices = {}
    for name in INDICES:
        completed_daily = daily_data.get(name)
        previous_sessions = (
            completed_daily.tail(7)
            if completed_daily is not None and not completed_daily.empty
            else pd.DataFrame()
        )
        previous_day = (
            completed_daily.iloc[-1]
            if completed_daily is not None and not completed_daily.empty
            else None
        )
        strategy_indices[name] = {
            "frames": {
                label: {
                    "analysis": analysis[name].get(label),
                    "candles": chart_data[name].get(label, []),
                }
                for label, _, _ in TIMEFRAMES
            },
            "pivots": pivots_from_daily(completed_daily) or {},
            "completed_sessions_count": len(previous_sessions),
            "previous_day_high": (
                float(previous_day["High"]) if previous_day is not None else None
            ),
            "previous_day_low": (
                float(previous_day["Low"]) if previous_day is not None else None
            ),
            "seven_day_high": (
                float(previous_sessions["High"].max())
                if not previous_sessions.empty else None
            ),
            "seven_day_low": (
                float(previous_sessions["Low"].min())
                if not previous_sessions.empty else None
            ),
        }

    global_lines, global_score, global_records = global_analysis()

    news_items, news_score = fetch_news()

    options = (
        fetch_fyers_options(spot_hint)
        or fetch_nse_fallback()
    )

    def score_of(item):
        return item["score"] if item else None

    bank_scores = [
        item["score"]
        for item in bank.values()
        if item
    ]

    components = [
        ("Nifty daily SMA trend", 15, score_of(nifty["Daily"])),
        (
            "Nifty 7-day momentum",
            10,
            seven["Nifty 50"]["score"]
            if seven["Nifty 50"]
            else None,
        ),
        ("Nifty 15m trend", 8, score_of(nifty["15m"])),
        ("Nifty 30m trend", 8, score_of(nifty["30m"])),
        ("Nifty 1h trend", 9, score_of(nifty["1h"])),
        (
            "Bank Nifty confirmation",
            10,
            sum(bank_scores) / len(bank_scores)
            if bank_scores
            else None,
        ),
        (
            "PCR",
            10,
            options["pcr_score"]
            if options
            else None,
        ),
        (
            "ATM OI flow & buildup",
            10,
            options["oi_score"]
            if options
            else None,
        ),
        ("Global markets", 10, global_score),
        ("News sentiment", 10, news_score),
    ]

    available = [
        (name, weight, score)
        for name, weight, score in components
        if score is not None
    ]

    weight_sum = sum(
        weight
        for _, weight, _ in available
    )

    total = (
        sum(weight * score for _, weight, score in available)
        / weight_sum
        * 100
        if weight_sum
        else 0.0
    )

    try:
        account_size = float(os.getenv("STRATEGY_ACCOUNT_SIZE", ""))
        if account_size <= 0:
            account_size = None
    except ValueError:
        account_size = None
    strategy_now = datetime.datetime.now(IST)
    strategy, next_strategy_state = evaluate_strategy(
        now=strategy_now,
        indices=strategy_indices,
        pivots=pivots,
        options=options,
        previous=prior_strategy_state.get("option_snapshot"),
        state=prior_strategy_state,
        market_bias=total,
        account_size=account_size,
        lot_size=(options or {}).get("lot_size"),
    )
    if options and options.get("source") == "Fyers" and options.get("chain"):
        next_strategy_state["option_snapshot"] = {
            "timestamp": options.get("captured_at"),
            "expiry": options.get("expiry"),
            "chain": options["chain"],
        }
    persist_strategy_state(next_strategy_state, strategy)

    # --------------------------------------------------------
    # BUILD TELEGRAM REPORT
    # --------------------------------------------------------

    lines = []

    lines.append("🧭 *LIVE MARKET BIAS*")

    lines.append(
        "_%s IST • NSE: %s_"
        % (
            now.strftime("%Y-%m-%d %H:%M"),
            status,
        )
    )

    lines.append(
        "*Overall: %s (score %+.0f/100)*"
        % (overall_label(total), total)
    )

    lines.append(
        "_Signal coverage: %.0f%% of weight available_"
        % weight_sum
    )

    if _fyers_error:
        lines.append(
            "⚠️ *Fyers unavailable*: `%s`"
            % safe_md(_fyers_error)[:200]
        )

    ranked = sorted(
        available,
        key=lambda item: item[1] * item[2],
        reverse=True,
    )

    bulls = [
        name
        for name, weight, score in ranked
        if score > 0.2
    ][:2]

    bears = [
        name
        for name, weight, score in reversed(ranked)
        if score < -0.2
    ][:2]

    if bulls:
        lines.append("🟢 Supporting: " + ", ".join(bulls))

    if bears:
        lines.append("🔴 Pressuring: " + ", ".join(bears))

    lines.append("\n*Score breakdown* (contribution/weight)")

    for name, weight, score in components:
        if score is None:
            lines.append("  • %s: n/a" % name)
        else:
            lines.append(
                "  • %s: %+.1f/%d"
                % (name, weight * score, weight)
            )

    lines.append("\n*1. Key levels (Nifty 50)*")

    if pivots:
        lines.append(
            "  • Pivots: R2 %s | R1 %s | P %s | S1 %s | S2 %s"
            % tuple(
                format(pivots[key], ",.0f")
                for key in ("R2", "R1", "P", "S1", "S2")
            )
        )

    if options:
        lines.append(
            "  • OI wall: Resistance %s | Support %s"
            % (
                format(options["resistance"], ",.0f"),
                format(options["support"], ",.0f"),
            )
        )

    if not pivots and not options:
        lines.append("  • unavailable")

    lines.append("\n*2. Last 7 sessions*")

    for name in ("Nifty 50", "Bank Nifty", "Nifty Midcap"):
        summary = seven[name]

        if not summary:
            lines.append("  • *%s*: data unavailable" % name)
            continue

        lines.append(
            "  • *%s*: net %+.2f%% | up days %d/7 | "
            "range %s–%s | at %.0f%% of range"
            % (
                name,
                summary["net"],
                summary["up"],
                format(summary["lo"], ",.0f"),
                format(summary["hi"], ",.0f"),
                summary["pos"],
            )
        )

        lines.append(
            "    Daily %%: %s"
            % " ".join(
                "%+.1f" % change
                for change in summary["chg"]
            )
        )

    lines.append(
        "\n*3. Trend by timeframe* (▲ above SMA, ▼ below)"
    )

    for name in INDICES:
        lines.append("*%s*" % name)

        for label, _, _ in TIMEFRAMES:
            lines.append(
                tf_line(label, analysis[name][label])
            )

    lines.append(
        "\n*4. Options: ATM premium, OI & PCR (Nifty)*"
    )

    lines.extend(render_options(options))

    lines.append("\n*5. World markets*")
    lines.extend(global_lines)

    lines.append("\n*6. News to watch*")
    lines.extend(render_news(news_items))

    lines.append("\n_%s_" % WEIGHTS_NOTE)
    lines.extend(format_strategy_report(strategy))

    # --------------------------------------------------------
    # EXPORT DASHBOARD DATA
    # --------------------------------------------------------

    payload = build_dashboard_payload(
        now=now,
        analysis=analysis,
        chart_data=chart_data,
        seven=seven,
        pivots=pivots,
        options=options,
        global_records=global_records,
        news_items=news_items,
        news_score=news_score,
        global_score=global_score,
        components=components,
        total=total,
        weight_sum=weight_sum,
        status=status,
        strategy=strategy,
        live_quotes=live_quotes,
        ltp_captured_at=ltp_captured_at,
    )

    save_dashboard_snapshot(payload)

    # --------------------------------------------------------
    # SEND TELEGRAM
    # --------------------------------------------------------

    send_telegram_message("\n".join(lines))


if __name__ == "__main__":
    main()