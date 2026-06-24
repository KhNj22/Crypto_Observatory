"""
Crypto Observatory V1 — data_sources.py
عملاء البيانات المجانية (مُتحقَّق منها): Binance · DefiLlama · alternative.me

ملاحظة: هذه الدوال تتصل بالإنترنت — تُشغَّل على جهازك حيث النطاقات متاحة.
كلها مجانية بلا مفتاح API (عدا أن Binance قد يتطلّب تبديل بورصة عبر ccxt حسب موقعك).
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd
import requests

import config as C

# نقاط النهاية المُتحقَّق منها
# واجهة Binance العامة للبيانات السوقية فقط — غير محجوبة جغرافياً (تعمل من السحابة
# الأمريكية ومن أي مكان). نفس مسارات /api/v3 بلا مصادقة. تتجنّب HTTP 451 على GitHub Actions.
BINANCE_BASE      = "https://data-api.binance.vision"
BINANCE_KLINES    = BINANCE_BASE + "/api/v3/klines"
DEFILLAMA_STABLE  = "https://stablecoins.llama.fi/stablecoincharts/all"
FNG_URL           = "https://api.alternative.me/fng/"

_HEADERS = {"User-Agent": "crypto-observatory/1.0"}
_TIMEOUT = 20


# ===========================================================================
# Binance — شموع OHLCV (المصدر الأساسي للسعر/الحجم)
# ===========================================================================
_KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume",
               "close_time", "quote_volume", "trades",
               "taker_base", "taker_quote", "ignore"]


def fetch_binance_klines(symbol: str, interval: str = "1d",
                         limit: int = 400, cache: bool = True) -> pd.DataFrame:
    """
    يجلب شموع OHLCV من Binance. الحد الأقصى 1000 شمعة/طلب (limit=400 يكفي SMA200).
    يُرجع DataFrame مفهرساً بالتاريخ UTC، ويخزّن نسخة Parquet للأرشيف/الـ backtest.
    """
    params = {"symbol": symbol.upper(), "interval": interval,
              "limit": min(int(limit), 1000)}
    r = requests.get(BINANCE_KLINES, params=params,
                     headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    raw = r.json()
    if not raw:
        raise RuntimeError(f"Binance أرجع بيانات فارغة لـ {symbol}")

    df = pd.DataFrame(raw, columns=_KLINE_COLS)
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["date", "open", "high", "low", "close", "volume", "quote_volume"]]
    df = df.set_index("date").sort_index()

    if cache:
        try:
            path = C.OHLCV_DIR / f"{symbol.upper()}_{interval}.parquet"
            df.to_parquet(path)   # يتطلّب pyarrow
        except Exception as e:    # الأرشفة لا يجب أن تُسقط التشغيل
            print(f"[تحذير] تعذّر حفظ Parquet لـ {symbol}: {e}")
    return df


def fetch_last_close(symbol: str, interval: str = "1d") -> float:
    """آخر سعر إغلاق (مثلاً ETHBTC لنسبة ETH/BTC)."""
    df = fetch_binance_klines(symbol, interval, limit=2, cache=False)
    return float(df["close"].iloc[-1])


# ===========================================================================
# Binance — الكون الديناميكي (رموز قابلة للتداول + حجم 24h للكل)
# ===========================================================================
BINANCE_EXINFO  = BINANCE_BASE + "/api/v3/exchangeInfo"
BINANCE_T24     = BINANCE_BASE + "/api/v3/ticker/24hr"


def fetch_binance_usdt_symbols(quote: str = "USDT") -> dict[str, dict]:
    """
    كل أزواج Spot الفعّالة المنتهية بـ quote (USDT). يُرجع: {pair: {base, quote}}.
    يستبعد أزواج SPOT غير المتداولة والأزواج غير الـ TRADING.
    """
    r = requests.get(BINANCE_EXINFO, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    out: dict[str, dict] = {}
    for s in r.json().get("symbols", []):
        if (s.get("status") == "TRADING" and s.get("quoteAsset") == quote
                and s.get("isSpotTradingAllowed", True)):
            out[s["symbol"]] = {"base": s["baseAsset"], "quote": s["quoteAsset"]}
    return out


def fetch_binance_24h_all() -> dict[str, dict]:
    """حجم/سعر 24h لكل الأزواج في طلب واحد. يُرجع: {pair: {quote_volume, last}}."""
    r = requests.get(BINANCE_T24, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    return {d["symbol"]: {"quote_volume": float(d.get("quoteVolume", 0) or 0),
                          "last": float(d.get("lastPrice", 0) or 0)}
            for d in data}


# ===========================================================================
# CoinGecko — القيمة السوقية + الهوية المستقرّة (coin_id) + العمر
# ===========================================================================
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"


def fetch_coingecko_markets(top_n: int = 500, vs: str = "usd") -> list[dict]:
    """
    أفضل top_n عملة بالقيمة السوقية. يُرجع قائمة فيها:
    {coin_id, symbol, name, market_cap, market_cap_rank, atl_date?, ...}.
    250/صفحة (طلب لكل 250). مفتاح Demo اختياري عبر env COINGECKO_API_KEY.
    """
    import os
    key = os.getenv("COINGECKO_API_KEY")
    headers = dict(_HEADERS)
    if key:
        headers["x-cg-demo-api-key"] = key
    out: list[dict] = []
    pages = (top_n + 249) // 250
    for page in range(1, pages + 1):
        params = {"vs_currency": vs, "order": "market_cap_desc",
                  "per_page": 250, "page": page, "sparkline": "false"}
        resp = requests.get(COINGECKO_MARKETS, params=params,
                            headers=headers, timeout=30)
        resp.raise_for_status()
        for c in resp.json():
            out.append({
                "coin_id": c.get("id"),
                "symbol": (c.get("symbol") or "").upper(),
                "name": c.get("name"),
                "market_cap": c.get("market_cap") or 0,
                "market_cap_rank": c.get("market_cap_rank"),
                "atl_date": c.get("atl_date"),
            })
        time.sleep(1.5)   # احترام حدّ المعدّل المجاني
    return out


# ===========================================================================
# DefiLlama — القيمة السوقية للعملات المستقرّة (مدخل State Machine — تاريخي مجاني)
# ===========================================================================
def fetch_stablecoin_mcap() -> dict:
    """
    يُرجع: {'mcap': القيمة الحالية, 'trend_30d': نسبة تغيّر آخر 30 يوم}.
    نجمع كل قيم totalCirculatingUSD (بالدولار) لكل نقطة يومية.
    """
    r = requests.get(DEFILLAMA_STABLE, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError("DefiLlama أرجع شكلاً غير متوقّع للعملات المستقرّة")

    def _usd(point: dict) -> float:
        block = point.get("totalCirculatingUSD") or {}
        if isinstance(block, dict):
            return float(sum(v for v in block.values() if isinstance(v, (int, float))))
        return float(block or 0)

    series = [(_int(p.get("date")), _usd(p)) for p in data]
    series = [(t, v) for t, v in series if t and v > 0]
    series.sort()
    cur = series[-1][1]
    # نقطة ~30 يوماً للخلف
    cutoff = series[-1][0] - 30 * 86400
    past = next((v for t, v in reversed(series) if t <= cutoff), series[0][1])
    trend = (cur - past) / past if past else 0.0
    return {"mcap": cur, "trend_30d": trend}


# ===========================================================================
# alternative.me — Fear & Greed (تاريخي مجاني منذ 2018)
# ===========================================================================
def fetch_fear_greed() -> dict:
    """يُرجع: {'value': int 0-100, 'label': str}."""
    r = requests.get(FNG_URL, params={"limit": 1, "format": "json"},
                     headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        raise RuntimeError("alternative.me أرجع Fear & Greed فارغاً")
    d = data[0]
    return {"value": int(d.get("value", 0)),
            "label": d.get("value_classification", "")}


# ===========================================================================
# أدوات داخلية
# ===========================================================================
def _int(x) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0
