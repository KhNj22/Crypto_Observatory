"""
Crypto Observatory V1 — test_state_machine.py
اختبار منطق محرّك الحالة offline (بلا إنترنت) ببيانات تركيبية.

يبرهن: التأكيد، نطاق الـ hysteresis المحايد، ومناعة التحوّل ضد التذبذب اللحظي.
شغّل:  python test_state_machine.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import market_observatory as MO


def _series(values) -> pd.Series:
    idx = pd.date_range("2025-01-01", periods=len(values), freq="D", tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def _hr(t): print("\n" + "─" * 66 + f"\n  {t}\n" + "─" * 66)


def main() -> None:
    P = C.SMA_REGIME_PERIOD          # 200
    base = 100.0

    # ---------------------------------------------------------------
    _hr("1) سوق صاعد واضح فوق SMA200 → risk_on")
    # 250 يوماً صاعدة بثبات
    closes = _series(base + np.arange(250) * 0.5)
    smas = MO.sma(closes, P)
    d = MO.confirm_regime(closes, smas, prev_regime=C.REGIME_RISK_OFF)
    print(f"الحالة = {d['regime']}  | آخر إشارات = {d['signals']}")
    assert d["regime"] == C.REGIME_RISK_ON
    assert d["changed"] is True

    # ---------------------------------------------------------------
    _hr("2) سوق هابط واضح تحت SMA200 → risk_off")
    closes = _series(base + 60 - np.arange(250) * 0.4)   # يبدأ مرتفعاً ثم ينهار
    smas = MO.sma(closes, P)
    d = MO.confirm_regime(closes, smas, prev_regime=C.REGIME_RISK_ON)
    print(f"الحالة = {d['regime']}  | آخر إشارات = {d['signals']}")
    assert d["regime"] == C.REGIME_RISK_OFF

    # ---------------------------------------------------------------
    _hr("3) داخل نطاق الـ Hysteresis (±2%) → يُبقي الحالة السابقة")
    # سعر يساوي SMA تقريباً (ضمن النطاق المحايد)
    closes = _series(np.r_[base + np.arange(220) * 0.3,
                           np.full(30, base + 220 * 0.3)])  # يستقرّ قرب المتوسط
    smas = MO.sma(closes, P)
    # نجعل آخر القيم ضمن ±2% من SMA
    last_sma = smas.dropna().iloc[-1]
    closes.iloc[-C.CONFIRM_DAYS:] = last_sma * 1.005       # +0.5% فقط (محايد)
    d_on = MO.confirm_regime(closes, smas, prev_regime=C.REGIME_RISK_ON)
    d_off = MO.confirm_regime(closes, smas, prev_regime=C.REGIME_RISK_OFF)
    print(f"محايد + سابق risk_on  → {d_on['regime']}  (إشارات {d_on['signals']})")
    print(f"محايد + سابق risk_off → {d_off['regime']}")
    assert d_on["regime"] == C.REGIME_RISK_ON       # الاستمرارية
    assert d_off["regime"] == C.REGIME_RISK_OFF
    assert d_on["changed"] is False

    # ---------------------------------------------------------------
    _hr("4) تذبذب لحظي (يوم واحد تحت) لا يقلب الحالة — التأكيد يعمل")
    closes = _series(base + np.arange(250) * 0.5)          # صاعد (risk_on)
    smas = MO.sma(closes, P)
    # هبوط حادّ ليوم واحد فقط تحت النطاق ثم عودة
    closes.iloc[-1] = smas.dropna().iloc[-1] * 0.90        # -10% ليوم واحد
    d = MO.confirm_regime(closes, smas,
                          confirm_days=3, prev_regime=C.REGIME_RISK_ON)
    print(f"الحالة بعد بليب يوم واحد = {d['regime']}  | إشارات = {d['signals']}")
    # آخر 3 إشارات: [on, on, off] → مختلطة → لا تحوّل → يبقى risk_on
    assert d["regime"] == C.REGIME_RISK_ON
    assert d["changed"] is False

    # ---------------------------------------------------------------
    _hr("5) لقطة market_state كاملة")
    closes = _series(base + np.arange(250) * 0.5)
    df = pd.DataFrame({"close": closes})
    df["open"] = df["high"] = df["low"] = df["close"]
    df["volume"] = 1000.0
    state = MO.compute_market_state(
        df,
        stablecoin={"mcap": 1.6e11, "trend_30d": 0.02},
        fear_greed={"value": 70, "label": "Greed"},
        eth_btc=0.053, prev_regime=C.REGIME_RISK_OFF)
    print(f"regime={state['regime']}  btc_close={state['btc_close']}  "
          f"sma200={state['btc_sma200']}  dist={state['btc_dist_pct']*100:+.1f}%")
    print(f"F&G={state['fear_greed']}  stable_trend={state['stablecoin_trend_30d']}")
    assert state["regime"] == C.REGIME_RISK_ON
    assert state["btc_sma200"] > 0

    _hr("النتيجة")
    print("جميع اختبارات محرّك الحالة نجحت ✓")
    print("الـ hysteresis + التأكيد يعملان: لا whipsaw، والتحوّل يتطلّب استمراريّة.")


if __name__ == "__main__":
    main()
