"""
Crypto Observatory V1 — market_observatory.py
محرّك الحالة (State Machine) — لا Score مرجّح.

المنطق المثبَّت: BTC مقابل SMA200 هو القلب، مع:
  • Hysteresis: نطاق ±X% حول SMA200 (منطقة محايدة) لقتل الـ whipsaw.
  • Confirmation: التحوّل لا يلتزم إلا بعد N أيام متتالية من الإشارة الجديدة.
البنود الأخرى (Stablecoin trend, F&G) سياقية. الـ Breadth يُضاف عند توفّر الكون (مرحلة لاحقة).
"""

from __future__ import annotations

import pandas as pd

import config as C


# ===========================================================================
# أدوات
# ===========================================================================
def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def raw_signal(close: float, sma_val: float, hyst: float) -> str:
    """
    الإشارة الخام ليوم واحد ضمن نطاق الـ hysteresis:
      فوق sma*(1+hyst) → risk_on ، تحت sma*(1-hyst) → risk_off ، غير ذلك → neutral
    """
    if pd.isna(sma_val):
        return "neutral"
    if close > sma_val * (1 + hyst):
        return C.REGIME_RISK_ON
    if close < sma_val * (1 - hyst):
        return C.REGIME_RISK_OFF
    return "neutral"


def confirm_regime(closes: pd.Series, smas: pd.Series, *,
                   hyst: float = C.HYSTERESIS_PCT,
                   confirm_days: int = C.CONFIRM_DAYS,
                   prev_regime: str | None = None) -> dict:
    """
    يحدّد الحالة المُلتزَمة بتطبيق الـ hysteresis + التأكيد:
      • إذا كانت آخر `confirm_days` إشارات خام كلها risk_on  → risk_on
      • إذا كانت كلها risk_off → risk_off
      • غير ذلك (مختلطة/محايدة) → نُبقي prev_regime (الاستمرارية)
    prev_regime الافتراضي risk_off (تحفّظي) إن لم يوجد تاريخ.
    """
    prev = prev_regime or C.REGIME_RISK_OFF
    n = max(1, int(confirm_days))

    # آخر n إشارات خام
    recent_close = closes.dropna().iloc[-n:]
    recent_sma = smas.reindex(recent_close.index)
    signals = [raw_signal(c, s, hyst)
               for c, s in zip(recent_close.values, recent_sma.values)]

    last_raw = signals[-1] if signals else "neutral"
    if signals and all(s == C.REGIME_RISK_ON for s in signals):
        regime = C.REGIME_RISK_ON
    elif signals and all(s == C.REGIME_RISK_OFF for s in signals):
        regime = C.REGIME_RISK_OFF
    else:
        regime = prev   # لم يتأكّد تحوّل → استمرارية

    changed = regime != prev
    return {"regime": regime, "regime_raw": last_raw,
            "changed": changed, "signals": signals, "prev_regime": prev}


# ===========================================================================
# تجميع لقطة market_state الكاملة
# ===========================================================================
def compute_market_state(btc_df: pd.DataFrame, *,
                         stablecoin: dict | None = None,
                         fear_greed: dict | None = None,
                         eth_btc: float | None = None,
                         prev_regime: str | None = None,
                         ts_utc: str | None = None) -> dict:
    """
    يبني حمولة market_state من بيانات BTC + السياق.
    btc_df: DataFrame فيه عمود 'close' مفهرس بالتاريخ (يومي).
    """
    close = btc_df["close"].astype(float)
    smas = sma(close, C.SMA_REGIME_PERIOD)

    if smas.dropna().empty:
        raise ValueError(
            f"بيانات BTC غير كافية لحساب SMA{C.SMA_REGIME_PERIOD} "
            f"(لديك {len(close)} شمعة، تحتاج ≥ {C.SMA_REGIME_PERIOD}).")

    decision = confirm_regime(close, smas, prev_regime=prev_regime)

    last_close = float(close.iloc[-1])
    last_sma = float(smas.dropna().iloc[-1])
    dist_pct = (last_close - last_sma) / last_sma

    state = {
        "ts_utc": ts_utc or C.now_iso(),
        "regime": decision["regime"],
        "regime_raw": decision["regime_raw"],
        "btc_close": round(last_close, 2),
        "btc_sma200": round(last_sma, 2),
        "btc_dist_pct": round(dist_pct, 4),
        "hysteresis_pct": C.HYSTERESIS_PCT,
        "confirm_days": C.CONFIRM_DAYS,
        "eth_btc": eth_btc,
    }
    if stablecoin:
        state["stablecoin_mcap"] = stablecoin.get("mcap")
        state["stablecoin_trend_30d"] = stablecoin.get("trend_30d")
    if fear_greed:
        state["fear_greed"] = fear_greed.get("value")
        state["fear_greed_label"] = fear_greed.get("label")

    state["_changed"] = decision["changed"]      # للتقرير (لا يُخزَّن)
    state["_prev_regime"] = decision["prev_regime"]
    return state
