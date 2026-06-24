"""
Crypto Observatory V1 — universe.py  (المرحلة pre-2: الكون الديناميكي)
يستبدل القائمة الثابتة بمسح شامل + فلترة سيولة صلبة + قائمة استبعاد + تصنيف قطاعي.

التدفّق:
  مسح أزواج USDT (Binance) + حجم 24h (طلب واحد)
  ⨯ ضمّ القيمة السوقية + coin_id من CoinGecko (يحلّ تصادم الرموز بالأعلى mcap)
  ⨯ استبعاد (stable/wrapped/leveraged) ⨯ أرضية ميتة $1M ⨯ طبقة جودة ($200M/$5M)
  → قائمة الكون النهائية + قُمع (funnel) شفّاف.
"""

from __future__ import annotations

import json
import re

import config as C

_LEV_RE = [re.compile(p) for p in C.LEVERAGED_PATTERNS]


def load_sectors() -> dict:
    try:
        raw = json.loads((C.ROOT_DIR / "sectors.json").read_text(encoding="utf-8"))
        return {k.upper(): v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def _is_excluded(base: str) -> str | None:
    """يُرجع سبب الاستبعاد أو None."""
    if base in C.STABLECOINS or base in C.WRAPPED:
        return C.FAIL_EXCLUDED
    if any(rx.match(base) for rx in _LEV_RE):
        return C.FAIL_EXCLUDED
    return None


def classify_universe(binance_pairs: dict, tickers: dict,
                      cg_markets: list, sectors: dict | None = None) -> dict:
    """
    دالة نقية (قابلة للاختبار): تأخذ البيانات المجلوبة وتُرجع:
      {"rows": [...], "funnel": {...}}
    كل صف: coin_id, symbol, sector, market_cap, volume_24h, rank_by_mcap,
            pair, in_universe(0/1), fail_reason.
    العمر يُطبَّق لاحقاً (بعد جلب الشموع) في المُنسّق.
    """
    sectors = sectors or {}

    # خريطة الرمز → أفضل عملة (أعلى mcap) لحلّ التصادم
    sym_best: dict[str, dict] = {}
    for m in cg_markets:
        sym = (m.get("symbol") or "").upper()
        if not sym:
            continue
        cur = sym_best.get(sym)
        if cur is None or (m.get("market_cap") or 0) > (cur.get("market_cap") or 0):
            sym_best[sym] = m

    rows: list[dict] = []
    f = {"scanned": 0, "excluded": 0, "no_mcap": 0, "dead": 0,
         "low_mcap": 0, "low_liq": 0, "passed": 0}
    mcap_gate, vol_gate = C.universe_gates()   # البوّابة النشطة حسب UNIVERSE_MODE

    for pair, meta in binance_pairs.items():
        base = meta["base"].upper()
        f["scanned"] += 1
        vol = (tickers.get(pair) or {}).get("quote_volume", 0.0)

        def add(in_u, reason, cg=None):
            rows.append({
                "coin_id": (cg or {}).get("coin_id") or base.lower(),
                "symbol": base,
                "sector": sectors.get(base, "Other") if in_u else None,
                "market_cap": (cg or {}).get("market_cap"),
                "volume_24h": vol,
                "rank_by_mcap": (cg or {}).get("market_cap_rank"),
                "pair": pair,
                "in_universe": int(in_u),
                "fail_reason": reason,
            })

        # 1) قائمة الاستبعاد
        ex = _is_excluded(base)
        if ex:
            f["excluded"] += 1; add(0, ex); continue

        # 2) لا قيمة سوقية (خارج Top500 CoinGecko = صغيرة جداً)
        cg = sym_best.get(base)
        if cg is None:
            f["no_mcap"] += 1; add(0, C.FAIL_NO_BINANCE); continue
        mcap = cg.get("market_cap") or 0

        # 3) أرضية ميتة $1M (vol أو mcap)
        if vol < C.DEAD_FLOOR_USD or mcap < C.DEAD_FLOOR_USD:
            f["dead"] += 1; add(0, C.FAIL_DEAD, cg); continue

        # 4) طبقة الجودة (البوّابة النشطة حسب الوضع)
        if mcap < mcap_gate:
            f["low_mcap"] += 1; add(0, C.FAIL_LOW_MCAP, cg); continue
        if vol < vol_gate:
            f["low_liq"] += 1; add(0, C.FAIL_LOW_LIQUIDITY, cg); continue

        # ✓ اجتاز
        f["passed"] += 1; add(1, None, cg)

    # حدّ Top_N بالقيمة السوقية (الفائض = rank_out)
    passed = [r for r in rows if r["in_universe"]]
    passed.sort(key=lambda r: (r["market_cap"] or 0), reverse=True)
    for i, r in enumerate(passed):
        if i >= C.UNIVERSE_TOP_N:
            r["in_universe"] = 0
            r["fail_reason"] = C.FAIL_RANK_OUT
            r["sector"] = None
            f["passed"] -= 1

    return {"rows": rows, "funnel": f}


def fetch_and_build() -> dict:
    """يجلب من الشبكة ثم يصنّف (يعمل على جهازك)."""
    import data_sources as DS
    pairs = DS.fetch_binance_usdt_symbols(C.QUOTE_ASSET)
    tickers = DS.fetch_binance_24h_all()
    cg = DS.fetch_coingecko_markets(top_n=500)
    return classify_universe(pairs, tickers, cg, load_sectors())
