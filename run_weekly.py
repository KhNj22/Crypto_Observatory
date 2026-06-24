"""
Crypto Observatory V1 — run_weekly.py  (المُنسّق الأسبوعي — المراحل 2 + 3)
يربط: الكون الديناميكي ← القطاعات ← الترتيب ← لقطة أسبوعية ← الداش بورد.

شغّل:  python run_weekly.py
يتصل بالإنترنت (Binance + CoinGecko) — يعمل على جهازك.
"""

from __future__ import annotations

import time

import archive as A
import config as C
import coin_ranking as CR
import data_sources as DS
import market_observatory as MO
import portfolio as PF
import reporter as R
import sector_observatory as SO
import universe as U


def _prev_top_ids(conn) -> set:
    """coin_id ضمن Top آخر تشغيل أسبوعي (للـ buffer)."""
    row = conn.execute(
        "SELECT run_id FROM runs WHERE run_type='weekly' AND status='ok' "
        "ORDER BY run_date DESC, run_id DESC LIMIT 1").fetchone()
    if not row:
        return set()
    rows = conn.execute(
        "SELECT coin_id FROM selections WHERE run_id=? AND in_top=1",
        (row["run_id"],)).fetchall()
    return {r["coin_id"] for r in rows}


def _prev_regime(conn) -> str | None:
    row = conn.execute(
        "SELECT m.regime FROM market_state m JOIN runs r ON r.run_id=m.run_id "
        "WHERE r.status='ok' ORDER BY r.run_date DESC, r.run_id DESC LIMIT 1"
    ).fetchone()
    return row["regime"] if row else None


def _prev_portfolio(conn) -> dict:
    """{arm_code: (cash, {coin_id: units})} من آخر تشغيل أسبوعي له محفظة.
    أول تشغيل (لا محفظة) → فارغ، فتبدأ كل الأذرع من رأس المال الابتدائي."""
    row = conn.execute(
        "SELECT p.run_id FROM portfolio_state p JOIN runs r ON r.run_id=p.run_id "
        "WHERE r.run_type='weekly' AND r.status='ok' "
        "ORDER BY r.run_date DESC, r.run_id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    rid = row["run_id"]
    out: dict[str, list] = {}
    for ps in conn.execute(
        "SELECT a.code, p.cash FROM portfolio_state p JOIN arms a ON a.arm_id=p.arm_id "
        "WHERE p.run_id=?", (rid,)):
        out[ps["code"]] = [ps["cash"], {}]
    for h in conn.execute(
        "SELECT a.code, h.coin_id, h.units FROM holdings h JOIN arms a ON a.arm_id=h.arm_id "
        "WHERE h.run_id=?", (rid,)):
        if h["code"] in out:
            out[h["code"]][1][h["coin_id"]] = h["units"]
    return {k: (v[0], v[1]) for k, v in out.items()}


BTC_ID, ETH_ID = "bitcoin", "ethereum"   # معرّفات المعايير (تطابق CoinGecko)


def main(force: bool = False) -> dict:
    conn = A.connect(); A.init_schema(conn)
    prev_top = _prev_top_ids(conn)
    prev_reg = _prev_regime(conn)
    prev_pf = _prev_portfolio(conn)
    arm_ids = {a[0]: A.arm_id_by_code(conn, a[0]) for a in C.ARM_DEFINITIONS}
    conn.close()

    # 1) الكون الديناميكي (مسح + فلترة + استبعاد)
    print("[1/5] بناء الكون الديناميكي…")
    built = U.fetch_and_build()
    rows, funnel = built["rows"], built["funnel"]
    print(f"      مسح {funnel['scanned']} زوج → نجح {funnel['passed']} "
          f"(استُبعد {funnel['excluded']}, ميتة {funnel['dead']}, "
          f"mcap منخفض {funnel['low_mcap']}, سيولة منخفضة {funnel['low_liq']})")

    # 2) جلب الشموع للمرشّحين + فلتر العمر
    print("[2/6] جلب الشموع وتطبيق فلتر العمر…")
    btc = DS.fetch_binance_klines("BTCUSDT", "1d", limit=400)
    btc_close = btc["close"].astype(float)
    try:
        eth_last = float(DS.fetch_binance_klines("ETHUSDT", "1d", limit=400)["close"].iloc[-1])
    except Exception:
        eth_last = None
    prices: dict = {}
    for r in [x for x in rows if x["in_universe"]]:
        try:
            df = DS.fetch_binance_klines(r["pair"], "1d", limit=400, cache=False)
        except Exception as e:
            r["in_universe"] = 0; r["fail_reason"] = C.FAIL_NO_BINANCE
            continue
        if len(df) < C.MIN_AGE_DAYS:           # أصغر من سنة
            r["in_universe"] = 0; r["fail_reason"] = C.FAIL_TOO_YOUNG
            continue
        r["age_days"] = len(df)
        prices[r["coin_id"]] = df
        time.sleep(0.05)                       # throttle مهذّب
    n_uni = sum(1 for r in rows if r["in_universe"])
    print(f"      الكون النهائي بعد العمر: {n_uni} عملة")

    # 3) المرحلة 2 — القطاعات
    print("[3/6] حساب القطاعات (Sector RS)…")
    sec = SO.compute_sectors(rows, prices)
    sectors = [{**s, "run_id": None} for s in sec["sectors"]]
    for s in sectors:
        s.pop("run_id", None); s.pop("n_members", None)

    # 4) المرحلة 3 — الترتيب + Buffers
    print("[4/6] التقييم متعدد العوامل + Buffers…")
    rank = CR.compute_ranking(rows, prices, btc_close, prev_top_ids=prev_top)

    # حالة السوق مع Breadth الحقيقي الآن
    ms = MO.compute_market_state(btc, prev_regime=prev_reg)
    if sec["universe_breadth"] is not None:
        ms["breadth_pct_above_sma"] = round(sec["universe_breadth"], 4)
    regime = ms["regime"]

    # 5) المرحلة 5 — المحفظة الورقية (كل الأذرع، رسوم + slippage)
    print("[5/6] إعادة توازن المحفظة الورقية (10 أذرع)…")
    price_now = {cid: float(df["close"].iloc[-1]) for cid, df in prices.items()}
    price_now[BTC_ID] = float(btc_close.iloc[-1])
    if eth_last is not None:
        price_now[ETH_ID] = eth_last
    symbols = {r["coin_id"]: r["symbol"] for r in rows}
    symbols[BTC_ID] = "BTC"; symbols[ETH_ID] = "ETH"
    uni_ids = [r["coin_id"] for r in rows if r["in_universe"]]
    atr = {m["coin_id"]: m.get("atr_pct") for m in rank["coin_metrics"]}
    sels = [dict(s, atr_pct=atr.get(s["coin_id"])) for s in rank["selections"]]

    pf_state, holdings, trades = [], [], []
    for arm in C.ARM_DEFINITIONS:
        code = arm[0]; aid = arm_ids[code]
        pc, pu = prev_pf.get(code, (C.INITIAL_CAPITAL, {}))
        weights = PF.target_weights(arm, sels, sectors, regime, BTC_ID, ETH_ID, uni_ids)
        st = PF.rebalance(pu, pc, weights, price_now)
        pf_state.append({"arm_id": aid, "cash": st["cash"],
                         "invested_value": st["invested_value"], "nav": st["nav"],
                         "n_positions": st["n_positions"], "regime_at_run": regime,
                         "turnover": st["turnover"], "fees_paid": st["fees_paid"]})
        holdings += PF.holdings_rows(aid, weights, st, price_now, symbols)
        trades += [{**t, "arm_id": aid, "symbol": symbols.get(t["coin_id"])}
                   for t in st["trades"]]
    nav_by = {arm_ids_inv: p for arm_ids_inv, p in
              zip([a[0] for a in C.ARM_DEFINITIONS], pf_state)}
    print(f"      NAV: C={nav_by['C']['nav']:.0f}  Golden Core(D)={nav_by['D']['nav']:.0f}  "
          f"BTC(G)={nav_by['G']['nav']:.0f}  (regime={regime})")

    # 6) لقطة أسبوعية ذرّية + داش بورد
    print("[6/6] كتابة اللقطة + توليد الداش بورد…")
    # سجّل كل عملة ممسوحة في coins (سجلّ كامل) — الفاشلة عملات حقيقية أيضاً،
    # ووجودها يمنع انتهاك FK لصفوف universe (سجلّ survivorship).
    coins = [{"coin_id": r["coin_id"], "symbol": r["symbol"]} for r in rows]
    coins.append({"coin_id": BTC_ID, "symbol": "BTC"})
    if eth_last is not None:
        coins.append({"coin_id": ETH_ID, "symbol": "ETH"})
    payload = {
        "coins": coins,
        "market_state": {k: v for k, v in ms.items() if not k.startswith("_")},
        "universe": rows,                       # كل المسح (سجلّ survivorship كامل)
        "sector_metrics": sectors,
        "coin_metrics": rank["coin_metrics"],
        "selections": rank["selections"],
        "portfolio_state": pf_state,            # المرحلة 5 — NAV لكل ذراع
        "holdings": holdings,
        "trades": trades,
    }
    res = A.snapshot(C.today_utc(), "weekly", payload, force=force)
    print(f"      [أرشيف] {res['status']}  run_id={res.get('run_id')}")

    R.generate()
    return res


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
