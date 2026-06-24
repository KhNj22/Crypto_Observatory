"""
Crypto Observatory V1 — coin_ranking.py  (المرحلة 3)
تقييم متعدد العوامل (5 عوامل) + فرز Top20 + نظام Buffers (enter/hold/exit).

مبدأ المتانة: تركيب رُتَبي (rank-based) لا أوزان سحرية + قمع الشواذّ.
تذكير صريح: هذا cross-sectional RS — هو بالضبط ما يجب أن يثبت Paper الحي إن كان له edge.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I


def apply_buffers(rank_of: dict, prev_top: set,
                  top_n: int = C.TOP_N, entry: int = C.ENTRY_RANK,
                  exit: int = C.EXIT_RANK) -> set:
    """
    نظام Entry/Exit Buffer (يقلّل الـ Turnover):
      • يبقى المركز السابق ما دام ضمن EXIT_RANK (≤30).
      • يدخل مركز جديد فقط إن أصبح ضمن ENTRY_RANK (≤15).
      • ثم نأخذ أفضل top_n بالترتيب المركّب.
    يُرجع مجموعة coin_id ضمن Top.
    """
    eligible_hold = {c for c in prev_top if rank_of.get(c, 1e9) <= exit}
    eligible_enter = {c for c, rk in rank_of.items() if rk <= entry}
    keep = eligible_hold | eligible_enter
    return set(sorted(keep, key=lambda c: rank_of[c])[:top_n])


def compute_ranking(universe_rows: list[dict], prices: dict[str, pd.DataFrame],
                    btc_close: pd.Series,
                    prev_top_ids: set | None = None) -> dict:
    """
    دالة نقية. يُرجع {"coin_metrics": [...], "selections": [...]}.
    prev_top_ids = مجموعة coin_id التي كانت ضمن Top الأسبوع الماضي (للـ buffer).
    """
    prev_top = prev_top_ids or set()
    members = [r for r in universe_rows if r.get("in_universe")]

    recs = []
    for r in members:
        df = prices.get(r["coin_id"])
        if df is None or len(df) < 60:        # نحتاج تاريخاً كافياً
            continue
        close = df["close"].astype(float)
        ef, es, up = I.trend_up(close)
        last = float(close.iloc[-1])
        trend_strength = (last / es - 1.0) if (es and not np.isnan(es)) else np.nan
        struct_score, hh_hl = I.structure_hh_hl(close)
        recs.append({
            "coin_id": r["coin_id"], "symbol": r["symbol"],
            "sector": r.get("sector") or "Other",
            "rank_by_mcap": r.get("rank_by_mcap"),
            "ema50": ef, "ema200": es, "trend_up": up,
            "f_trend": trend_strength,
            "f_rs": I.relative_strength(close, btc_close, C.RS_LOOKBACK_DAYS),
            "f_volume": I.volume_expansion(df["volume"].astype(float)),
            "f_structure": struct_score,
            "f_vola": I.volatility_score(df),       # انكماش (أعلى=أفضل)
            "rs_90d": I.relative_strength(close, btc_close, C.RS_LOOKBACK_DAYS),
            "atr_pct": I.atr_pct(df),
            "structure_hh_hl": hh_hl,
        })
    if not recs:
        return {"coin_metrics": [], "selections": []}

    df = pd.DataFrame(recs)

    # قمع الشواذّ لكل عامل ثم رتبة مئوية (الأعلى = أفضل = 100)
    factors = {"f_trend": "rk_trend", "f_rs": "rk_rs", "f_volume": "rk_volume",
               "f_structure": "rk_structure", "f_vola": "rk_vola"}
    for raw, rk in factors.items():
        df[rk] = I.rank_pct(I.winsorize(df[raw]), ascending=True)

    # التركيب: متوسط الرُتَب الخمس (وزن متساوٍ، متين)
    df["composite_score"] = df[list(factors.values())].mean(axis=1)
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df["composite_rank"] = np.arange(1, len(df) + 1)

    rank_of = dict(zip(df["coin_id"], df["composite_rank"]))

    # رُتب العوامل كأعداد (1=أفضل) للتخزين
    for raw, rk in factors.items():
        df[rk.replace("rk_", "frank_")] = df[rk].rank(ascending=False).astype(int)

    # --- نظام الـ Buffers ---
    in_top = apply_buffers(rank_of, prev_top)

    coin_metrics, selections = [], []
    for _, row in df.iterrows():
        cid = row["coin_id"]
        coin_metrics.append({
            "coin_id": cid, "sector": row["sector"],
            "rank_by_mcap": int(row["rank_by_mcap"]) if pd.notna(row["rank_by_mcap"]) else None,
            "ema50": _f(row["ema50"]), "ema200": _f(row["ema200"]),
            "trend_up": int(row["trend_up"]),
            "rs_90d": _f(row["rs_90d"]), "rs_vs_btc": _f(row["f_rs"]),
            "vol_expansion": _f(row["f_volume"]),
            "structure_score": _f(row["f_structure"]),
            "structure_hh_hl": int(row["structure_hh_hl"]),
            "atr": None, "atr_pct": _f(row["atr_pct"]),
            "factor_rank_trend": _i(row["frank_trend"]),
            "factor_rank_rs": _i(row["frank_rs"]),
            "factor_rank_volume": _i(row["frank_volume"]),
            "factor_rank_structure": _i(row["frank_structure"]),
            "factor_rank_vola": _i(row["frank_vola"]),
            "composite_rank": int(row["composite_rank"]),
        })

    # اختيارات: الداخل/الباقي + الخارج (من كان في السابق وخرج)
    exited = prev_top - in_top
    for cid in in_top:
        rrow = df[df["coin_id"] == cid].iloc[0]
        rk = rank_of[cid]
        selections.append({
            "coin_id": cid, "symbol": rrow["symbol"], "sector": rrow["sector"],
            "composite_rank": int(rk),
            "action": "hold" if cid in prev_top else "enter",
            "in_top": 1,
            "tier": 1 if rk <= 5 else (2 if rk <= 10 else 3),
        })
    for cid in exited:
        rrow = df[df["coin_id"] == cid]
        sym = rrow["symbol"].iloc[0] if not rrow.empty else None
        selections.append({
            "coin_id": cid, "symbol": sym, "sector": None,
            "composite_rank": int(rank_of.get(cid, 0)) or None,
            "action": "exit", "in_top": 0, "tier": None,
        })
    return {"coin_metrics": coin_metrics, "selections": selections}


def _f(x):
    return float(x) if (x is not None and not (isinstance(x, float) and np.isnan(x))) else None


def _i(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
