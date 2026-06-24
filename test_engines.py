"""
Crypto Observatory V1 — test_engines.py
اختبار منطق المرحلتين 2 و3 offline (بلا إنترنت) ببيانات تركيبية.

يبرهن: الفلترة/الاستبعاد، قُمع الكون، متانة Sector RS ضد الشواذّ،
التركيب الرُتَبي، ونظام الـ Buffers (enter/hold/exit).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import coin_ranking as CR
import sector_observatory as SO
import universe as U


def _hr(t): print("\n" + "─" * 66 + f"\n  {t}\n" + "─" * 66)


def _price(trend_per_day, n=260, start=100.0, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    base = start * np.cumprod(1 + trend_per_day + rng.normal(0, noise, n))
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": base, "high": base*1.01, "low": base*0.99,
                         "close": base, "volume": 1e6*(1+rng.normal(0,0.1,n))},
                        index=idx)


def main() -> None:
    # ===============================================================
    _hr("1) الاستبعاد + قُمع الكون (classify_universe)")
    pairs = {p+"USDT": {"base": p, "quote": "USDT"} for p in
             ["BTC","SOL","USDC","BTCUP","SMALL","ILLIQ","DEAD","NOCG"]}
    tickers = {
        "BTCUSDT":  {"quote_volume": 3e10}, "SOLUSDT": {"quote_volume": 3e9},
        "USDCUSDT": {"quote_volume": 5e10}, "BTCUPUSDT": {"quote_volume": 1e7},
        "SMALLUSDT":{"quote_volume": 1e7},  "ILLIQUSDT": {"quote_volume": 2e6},
        "DEADUSDT": {"quote_volume": 5e5},  "NOCGUSDT": {"quote_volume": 1e8},
    }
    cg = [
        {"coin_id":"bitcoin","symbol":"BTC","market_cap":1.2e12,"market_cap_rank":1},
        {"coin_id":"solana","symbol":"SOL","market_cap":7e10,"market_cap_rank":5},
        {"coin_id":"small","symbol":"SMALL","market_cap":5e7,"market_cap_rank":400},
        {"coin_id":"illiq","symbol":"ILLIQ","market_cap":3e8,"market_cap_rank":150},
        {"coin_id":"dead","symbol":"DEAD","market_cap":4e5,"market_cap_rank":900},
    ]
    res = U.classify_universe(pairs, tickers, cg, {"BTC":"L1","SOL":"L1"})
    f = res["funnel"]
    in_uni = {r["symbol"] for r in res["rows"] if r["in_universe"]}
    reasons = {r["symbol"]: r["fail_reason"] for r in res["rows"]}
    print(f"القُمع: {f}")
    print(f"داخل الكون: {in_uni}")
    print(f"الأسباب: {reasons}")
    assert in_uni == {"BTC", "SOL"}
    assert f["excluded"] == 2 and f["dead"] == 1
    assert reasons["SMALL"] == C.FAIL_LOW_MCAP
    assert reasons["ILLIQ"] == C.FAIL_LOW_LIQUIDITY
    assert reasons["USDC"] == C.FAIL_EXCLUDED and reasons["BTCUP"] == C.FAIL_EXCLUDED

    # ===============================================================
    _hr("2) متانة Sector RS — قطاع قوي يتفوّق، والوسيط يقاوم الشاذّ")
    members = [{"coin_id":f"ai{i}","symbol":f"AI{i}","sector":"AI",
                "in_universe":1,"market_cap":1e9} for i in range(3)] + \
              [{"coin_id":f"mm{i}","symbol":f"MM{i}","sector":"Meme",
                "in_universe":1,"market_cap":1e9} for i in range(3)]
    prices = {}
    for i in range(3):
        prices[f"ai{i}"] = _price(+0.004, seed=10+i)   # AI صاعد
        prices[f"mm{i}"] = _price(-0.002, seed=20+i)   # Meme هابط
    # شاذّ: عملة Meme واحدة انفجرت — يجب ألا تقلب القطاع بفضل الوسيط
    prices["mm0"] = _price(+0.02, seed=99)
    out = SO.compute_sectors(members, prices)
    s = {x["sector"]: x for x in out["sectors"]}
    print(f"AI: rank={s['AI']['sector_rank']} score={s['AI']['sector_score']} rs90={s['AI']['rs_90d']:.3f}")
    print(f"Meme: rank={s['Meme']['sector_rank']} score={s['Meme']['sector_score']} rs90={s['Meme']['rs_90d']:.3f}")
    print(f"Breadth الكون: {out['universe_breadth']:.2f}")
    assert s["AI"]["sector_rank"] == 1            # القطاع القوي أولاً
    assert s["AI"]["rs_90d"] > s["Meme"]["rs_90d"]

    # ===============================================================
    _hr("3) التركيب الرُتَبي — الترتيب يُحسب ويُفرز")
    uni = [{"coin_id":f"c{i}","symbol":f"C{i}","sector":"L1",
            "in_universe":1,"rank_by_mcap":i+1} for i in range(6)]
    pr = {f"c{i}": _price(+0.006 - i*0.002, seed=30+i) for i in range(6)}  # c0 أقوى
    btc = _price(+0.001, seed=5)["close"]
    rk = CR.compute_ranking(uni, pr, btc, prev_top_ids=set())
    ranks = {m["coin_id"]: m["composite_rank"] for m in rk["coin_metrics"]}
    print(f"الرُتب المركّبة: {ranks}")
    assert ranks["c0"] < ranks["c5"]              # الأقوى رتبته أصغر (أفضل)
    assert sorted(ranks.values()) == list(range(1, 7))

    # ===============================================================
    _hr("4) نظام الـ Buffers (enter / hold / exit)")
    # رُتب مُصطنعة: 30 عملة
    rank_of = {f"x{i}": i for i in range(1, 31)}
    prev_top = {"x3", "x25", "x35"}               # x35 غير موجود الآن
    rank_of["x35"] = 35                            # كان بالأعلى، الآن رتبته 35 (>30)
    in_top = CR.apply_buffers(rank_of, prev_top)
    print(f"داخل Top الآن: {sorted(in_top, key=lambda c: rank_of[c])[:6]} … ({len(in_top)})")
    assert "x3" in in_top                          # رتبة 3 ≤ 15 → بقي
    assert "x25" in in_top                          # كان بالأعلى ورتبته 25 ≤ 30 → hold
    assert "x35" not in in_top                      # كان بالأعلى لكن 35 > 30 → exit
    assert "x12" in in_top                          # 12 ≤ 15 → enter
    assert "x18" not in in_top                      # 18 > 15 ولم يكن سابقاً → لا يدخل
    assert len(in_top) <= C.TOP_N
    print("enter/hold/exit يعملون بدقّة ✓")

    # ===============================================================
    _hr("النتيجة")
    print("جميع اختبارات المرحلتين 2 و3 نجحت ✓")
    print("الفلترة + متانة RS + التركيب الرُتَبي + Buffers — كلها صحيحة.")


if __name__ == "__main__":
    main()
