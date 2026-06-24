"""
Crypto Observatory V1 — portfolio.py  (المرحلة 5 — محرّك المحفظة الورقية، نقي)

يحوّل المرصد من "مراقبة" إلى "دليل قابل للاختبار":
  - target_weights(arm, …) → أوزان كل ذراع (10 أذرع) مع بوّابة الحالة.
  - rebalance(…)           → إعادة توازن من الحالة السابقة مع رسوم + slippage → NAV.

كل الدوال نقيّة (تُختبَر offline). التنفيذ الورقي فقط (assert_paper_only في الأرشيف).
أذرع الاستراتيجية: A, B, C, C_rp, C_tier.  المعايير: D(Golden Core), E, F, G, H.
"""

from __future__ import annotations

import config as C

EPS = 1e-9


# ===========================================================================
# 1) اختيار القطاعات القوية (لأذرع use_sector)
# ===========================================================================
def strong_sectors(sectors: list) -> set:
    """القطاعات ذات التدفّق: RS موجب. إن غابت كلّها، النصف الأعلى ترتيباً."""
    if not sectors:
        return set()
    pos = {s["sector"] for s in sectors if (s.get("rs_90d") or 0) > 0}
    if pos:
        return pos
    ranked = sorted(sectors, key=lambda s: s.get("sector_rank") or 1e9)
    return {s["sector"] for s in ranked[: max(1, len(ranked) // 2)]}


# ===========================================================================
# 2) مخطّطات الترجيح
# ===========================================================================
def _equal(ids: list) -> dict:
    return {i: 1.0 / len(ids) for i in ids} if ids else {}


def _risk_parity(rows: list) -> dict:
    """وزن عكسي للتقلّب (ATR%): أقلّ تقلّباً → وزن أكبر (مساهمة مخاطرة متساوية)."""
    inv = {r["coin_id"]: 1.0 / max(r.get("atr_pct") or 0.05, 0.005) for r in rows}
    tot = sum(inv.values()) or 1.0
    return {k: v / tot for k, v in inv.items()}


def _tiered(rows: list) -> dict:
    """tier 1/2/3 → 40/40/20، موزّعة بالتساوي داخل كل tier.
    حصص الـ tiers الغائبة تُعاد توزيعها على الموجودة بالتناسب."""
    alloc = {1: 0.40, 2: 0.40, 3: 0.20}
    by_tier: dict[int, list] = {1: [], 2: [], 3: []}
    for r in rows:
        by_tier.get(r.get("tier") or 3, by_tier[3]).append(r["coin_id"])
    present = {t: ids for t, ids in by_tier.items() if ids}
    tot = sum(alloc[t] for t in present) or 1.0
    w: dict[str, float] = {}
    for t, ids in present.items():
        share = alloc[t] / tot
        for cid in ids:
            w[cid] = share / len(ids)
    return w


# ===========================================================================
# 3) أوزان الذراع المستهدفة (مع بوّابة الحالة)
# ===========================================================================
def target_weights(arm: tuple, selections: list, sectors: list, regime: str,
                   btc_id: str, eth_id: str, universe_ids: list | None = None,
                   top_n: int | None = None) -> dict:
    """يُرجع {coin_id: weight} (مجموعها ≤ 1؛ الباقي نقد). {} = نقد كامل."""
    code, _name, weighting, use_sector, use_market, _is_bench = arm
    top_n = top_n or C.TOP_N

    # بوّابة الحالة: الأذرع التي تتبع السوق تخرج نقداً عند risk_off
    if use_market and regime == C.REGIME_RISK_OFF:
        return {}

    # — المعايير الثابتة —
    if code == "D":   # Golden Core: BTC/ETH عند risk_on، نقد عند risk_off
        return {} if regime == C.REGIME_RISK_OFF else {btc_id: 0.5, eth_id: 0.5}
    if code == "E":   return {btc_id: 0.5, eth_id: 0.5}
    if code == "G":   return {btc_id: 1.0}
    if code == "H":   return {eth_id: 1.0}
    if code == "F":   # كامل الكون بالتساوي
        ids = universe_ids or [s["coin_id"] for s in selections if s.get("in_top")]
        return _equal(ids)

    # — أذرع الاستراتيجية A / B / C / C_rp / C_tier —
    cands = [s for s in selections if s.get("in_top")]
    if use_sector:
        strong = strong_sectors(sectors)
        filtered = [c for c in cands if c.get("sector") in strong]
        cands = filtered or cands          # لا نُفرّغ المحفظة تماماً لو ضاقت القطاعات
    cands = sorted(cands, key=lambda c: c.get("composite_rank") or 1e9)[:top_n]
    if not cands:
        return {}
    if weighting == "risk_parity":
        return _risk_parity(cands)
    if weighting == "tiered":
        return _tiered(cands)
    return _equal([c["coin_id"] for c in cands])


# ===========================================================================
# 4) إعادة التوازن مع التكلفة (rebalance)
# ===========================================================================
def _trade(cid, side, units, price, gross, fee_r, slip_r, reason) -> dict:
    cost_dir = 1.0 + (fee_r + slip_r) if side == "buy" else 1.0 - (fee_r + slip_r)
    return {"coin_id": cid, "side": side, "units": round(units, 10), "price": price,
            "gross_value": round(gross, 2), "fee": round(gross * fee_r, 4),
            "slippage": round(gross * slip_r, 4), "net_value": round(gross * cost_dir, 2),
            "reason": reason}


def rebalance(prev_units: dict, prev_cash: float, weights: dict,
              price_now: dict) -> dict:
    """
    يحسب الحالة الجديدة من السابقة بعد إعادة التوازن.
    التكلفة = (taker_fee + slippage) × قيمة كل صفقة، تُخصم من NAV.
    يُرجع: units, cash, invested_value, nav, turnover, fees_paid, n_positions, trades.
    """
    fee_r = C.TAKER_FEE
    slip_r = C.SLIPPAGE_BPS / 10000.0

    # 1) mark-to-market للمراكز الحالية
    cur_val = {cid: u * price_now[cid] for cid, u in prev_units.items()
               if cid in price_now and u > 0}
    invested0 = sum(cur_val.values())
    nav0 = prev_cash + invested0

    # 2) القيم المستهدفة بالدولار (على أساس nav0)
    tgt_val = {cid: w * nav0 for cid, w in weights.items() if cid in price_now}

    # 3) احسب الصفقات (الفرق بين الحالي والمستهدف)
    trades: list[dict] = []
    turnover_usd = 0.0
    cost = 0.0
    for cid in set(cur_val) | set(tgt_val):
        diff = tgt_val.get(cid, 0.0) - cur_val.get(cid, 0.0)
        if abs(diff) < EPS:
            continue
        side = "buy" if diff > 0 else "sell"
        px = price_now[cid]
        cost += abs(diff) * (fee_r + slip_r)
        turnover_usd += abs(diff)
        reason = "exit_cash" if (not weights and side == "sell") else "rebalance"
        trades.append(_trade(cid, side, abs(diff) / px, px, abs(diff),
                             fee_r, slip_r, reason))

    nav1 = nav0 - cost

    # 4) اشتقاق الوحدات الجديدة من الأوزان × nav1
    new_units = {cid: (w * nav1) / price_now[cid]
                 for cid, w in weights.items() if cid in price_now and w > 0}
    sw = sum(w for cid, w in weights.items() if cid in price_now)
    invested1 = sum(u * price_now[cid] for cid, u in new_units.items())
    cash1 = nav1 * (1.0 - sw)

    return {"units": new_units, "cash": round(cash1, 4),
            "invested_value": round(invested1, 4), "nav": round(nav1, 4),
            "turnover": round(turnover_usd / nav0, 4) if nav0 > 0 else 0.0,
            "fees_paid": round(cost, 4), "n_positions": len(new_units),
            "trades": trades}


# ===========================================================================
# 5) بناء صفوف holdings للأرشيف
# ===========================================================================
def holdings_rows(arm_id: int, weights: dict, state: dict, price_now: dict,
                  symbols: dict) -> list[dict]:
    nav = state["nav"] or 1.0
    rows = []
    for cid, units in state["units"].items():
        val = units * price_now[cid]
        rows.append({
            "arm_id": arm_id, "coin_id": cid, "symbol": symbols.get(cid),
            "target_weight": round(weights.get(cid, 0.0), 6),
            "actual_weight": round(val / nav, 6),
            "units": round(units, 10), "entry_price": price_now[cid],
            "mark_price": price_now[cid], "value": round(val, 4),
        })
    return rows
