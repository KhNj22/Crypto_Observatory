"""
Crypto Observatory V1 — sector_observatory.py  (المرحلة 2)
يحسب قوة القطاعات النسبية ويملأ sector_metrics لتتوهّج الخريطة الحرارية ببيانات حقيقية.

متانة (دروس Diamond): الوسيط + قمع الشواذّ (winsorize) — لا تهيمن عملة meme واحدة.
يُنتج أيضاً Breadth الكون الكلّي لتغذية State Machine في المرحلة 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I


def _above_ma(close: pd.Series, period: int = 50) -> int:
    if len(close) < period:
        return 0
    return int(close.iloc[-1] > close.rolling(period).mean().iloc[-1])


def _near_high(close: pd.Series, lookback: int = 90, pct: float = 0.05) -> int:
    w = close.iloc[-lookback:]
    if len(w) < lookback:
        return 0
    return int(close.iloc[-1] >= w.max() * (1 - pct))


def compute_sectors(universe_rows: list[dict],
                    prices: dict[str, pd.DataFrame]) -> dict:
    """
    دالة نقية. universe_rows = الصفوف داخل الكون (in_universe=1).
    prices = {coin_id: DataFrame[close, high, low, volume]}.
    يُرجع {"sectors": [...], "universe_breadth": float}.
    """
    members = [r for r in universe_rows if r.get("in_universe")]

    # مقاييس لكل عملة
    recs = []
    for r in members:
        df = prices.get(r["coin_id"])
        if df is None or df.empty:
            continue
        close = df["close"].astype(float)
        recs.append({
            "coin_id": r["coin_id"], "sector": r.get("sector") or "Other",
            "mcap": r.get("market_cap") or 0,
            "r30": I.pct_return(close, C.RS_LOOKBACK_SHORT),
            "r90": I.pct_return(close, C.RS_LOOKBACK_DAYS),
            "above_ma": _above_ma(close),
            "near_high": _near_high(close),
        })
    if not recs:
        return {"sectors": [], "universe_breadth": None}

    df = pd.DataFrame(recs)
    # قمع الشواذّ على العوائد عبر الكون كله
    df["r30w"] = I.winsorize(df["r30"])
    df["r90w"] = I.winsorize(df["r90"])

    uni_med_30 = df["r30w"].median()
    uni_med_90 = df["r90w"].median()
    universe_breadth = float(df["above_ma"].mean())
    total_mcap = df["mcap"].sum()

    # تجميع بالقطاع
    out = []
    for sector, g in df.groupby("sector"):
        n = len(g)
        if n < C.MIN_SECTOR_MEMBERS:
            continue
        agg30 = I.robust_mean(g["r30w"], weights=g["mcap"])
        agg90 = I.robust_mean(g["r90w"], weights=g["mcap"])
        out.append({
            "sector": sector,
            "n_members": n,
            "rs_30d": agg30 - uni_med_30 if not np.isnan(agg30) else np.nan,
            "rs_90d": agg90 - uni_med_90 if not np.isnan(agg90) else np.nan,
            "momentum": (agg30 - uni_med_30) if not np.isnan(agg30) else np.nan,
            "breadth": float(g["above_ma"].mean()),
            "new_high_ratio": float(g["near_high"].mean()),
            "mcap_share": float(g["mcap"].sum() / total_mcap) if total_mcap else 0.0,
        })
    if not out:
        return {"sectors": [], "universe_breadth": universe_breadth}

    sdf = pd.DataFrame(out)
    # score 0..100 من رتبة rs_90d (الأعلى = 100)، ثم rank
    sdf["sector_score"] = I.rank_pct(sdf["rs_90d"], ascending=True).round(0)
    sdf = sdf.sort_values("sector_score", ascending=False).reset_index(drop=True)
    sdf["sector_rank"] = np.arange(1, len(sdf) + 1)

    sectors = sdf.to_dict("records")
    for s in sectors:                       # تنظيف NaN لـ JSON/SQLite
        for k, v in list(s.items()):
            if isinstance(v, float) and np.isnan(v):
                s[k] = None
    return {"sectors": sectors, "universe_breadth": universe_breadth}
