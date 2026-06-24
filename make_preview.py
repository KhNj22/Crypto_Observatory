"""
Crypto Observatory V1 — make_preview.py  (أداة معاينة فقط — ليست جزءاً من المنصّة)
تزرع بيانات نموذجية واقعية (قطاعات + ترتيب + محفظة) وتولّد معاينة كاملة للداش بورد،
لترى التصميم النهائي قبل بناء المراحل 2-3-5. لا تستخدمها على الأرشيف الحقيقي.

شغّل:  python make_preview.py   →  docs/index_preview.html
"""
from __future__ import annotations
import numpy as np
import config as C
import archive as A
import reporter as R


def seed(conn):
    arm = lambda code: A.arm_id_by_code(conn, code)
    now = C.now_ms()

    # ---- 12 تشغيلاً أسبوعياً مع NAV للاستراتيجية C والمعيار D ----
    rng = np.random.default_rng(7)
    dates = [f"2026-{m:02d}-{d:02d}" for (m, d) in
             [(4,6),(4,13),(4,20),(4,27),(5,4),(5,11),(5,18),(5,25),
              (6,1),(6,8),(6,15),(6,22)]]
    nav_c, nav_d = 10000.0, 10000.0
    c_ret = rng.normal(0.014, 0.035, len(dates))   # استراتيجية: عائد أعلى، تقلّب أعلى
    d_ret = rng.normal(0.010, 0.022, len(dates))   # نواة ذهبية: أنعم
    run_ids = []
    for i, dt in enumerate(dates):
        cur = conn.execute(
            """INSERT INTO runs(run_date,run_type,started_at,completed_at,status,
               data_complete,content_hash,schema_version)
               VALUES(?,?,?,?, 'ok',1,?,?)""",
            (dt, "weekly", now, now, f"demo{i}", C.SCHEMA_VERSION))
        rid = cur.lastrowid; run_ids.append(rid)
        if i > 0:
            nav_c *= (1 + c_ret[i]); nav_d *= (1 + d_ret[i])
        for code, nav in (("C", nav_c), ("D", nav_d)):
            conn.execute(
                """INSERT INTO portfolio_state(run_id,arm_id,ts_utc,cash,
                   invested_value,nav,n_positions,regime_at_run,turnover,fees_paid)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (rid, arm(code), dt, nav*0.1, nav*0.9, round(nav,2),
                 5, "risk_on", 0.2, round(nav*0.001,2)))

    last = run_ids[-1]

    # ---- market_state لأحدث تشغيل ----
    conn.execute(
        """INSERT INTO market_state(run_id,ts_utc,regime,regime_raw,btc_close,
           btc_sma200,btc_dist_pct,hysteresis_pct,confirm_days,stablecoin_mcap,
           stablecoin_trend_30d,fear_greed,fear_greed_label,breadth_pct_above_sma)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (last, "2026-06-22T04:06:12+00:00", "risk_on", "risk_on", 64200, 58100,
         0.1051, 0.02, 2, 1.62e11, 0.018, 72, "Greed", 0.63))

    # ---- قطاعات (heatmap) ----
    sectors = [("AI",0.42,91,1),("RWA",0.31,86,2),("L1",0.18,78,3),
               ("DePIN",0.12,68,4),("DeFi",0.09,64,5),("L2",0.04,55,6),
               ("Infra",-0.02,48,7),("Gaming",-0.18,34,8),("Meme",-0.27,28,9)]
    for name, rs, sc, rk in sectors:
        conn.execute(
            """INSERT INTO sector_metrics(run_id,sector,rs_30d,rs_90d,momentum,
               breadth,new_high_ratio,mcap_share,sector_score,sector_rank)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (last, name, rs*0.8, rs, rs, 0.5+sc/250, sc/180, sc/600, sc, rk))

    # ---- عملات + ترتيب + اختيارات (Top) ----
    coins = [("bittensor","TAO","AI",0.58,"enter"),("near","NEAR","AI",0.44,"hold"),
             ("ondo-finance","ONDO","RWA",0.39,"enter"),("sui","SUI","L1",0.36,"hold"),
             ("render-token","RENDER","AI",0.33,"hold"),("chainlink","LINK","RWA",0.21,"hold"),
             ("sei-network","SEI","L1",0.17,"enter"),("fetch-ai","FET","AI",0.14,"hold"),
             ("injective","INJ","DeFi",0.08,"hold"),("aptos","APT","L1",-0.03,"exit")]
    for i, (cid, sym, sec, rs, act) in enumerate(coins, 1):
        conn.execute("INSERT OR IGNORE INTO coins(coin_id,name,symbol,created_at) "
                     "VALUES(?,?,?,?)", (cid, sym, sym, now))
        conn.execute(
            """INSERT INTO coin_metrics(run_id,coin_id,sector,rank_by_mcap,trend_up,
               rs_90d,atr_pct,composite_rank) VALUES(?,?,?,?,?,?,?,?)""",
            (last, cid, sec, i, 1 if rs>=0 else 0, rs, 0.04+abs(rs)*0.05, i))
        conn.execute(
            """INSERT INTO selections(run_id,coin_id,symbol,sector,composite_rank,
               action,in_top,tier) VALUES(?,?,?,?,?,?,1,?)""",
            (last, cid, sym, sec, i, act, 1 if i<=5 else (2 if i<=10 else 3)))
    # ---- قطاعات الأسبوع السابق (رُتب مختلفة → تظهر أسهم الدوران) ----
    prev_run = run_ids[-2]
    prev_ranks = {"L1":1,"AI":3,"RWA":2,"DePIN":5,"DeFi":4,
                  "L2":6,"Infra":7,"Gaming":8,"Meme":9}
    for name, rs, sc, rk in sectors:
        conn.execute(
            """INSERT INTO sector_metrics(run_id,sector,rs_90d,sector_score,sector_rank)
               VALUES(?,?,?,?,?)""",
            (prev_run, name, rs*0.9, sc-3, prev_ranks.get(name, rk)))

    # ---- قُمع الكون: 10 مسمّاة + تعبئة واقعية + دلاء الفشل ----
    funnel_fill = [("dead_floor",95),("low_mcap",70),("low_liquidity",40),
                   ("excluded",25),("no_binance",30)]
    # 200 داخل الكون (10 مسمّاة + 190 صورية للعدّ)
    for i in range(190):
        cid = f"_dummy_pass_{i}"
        conn.execute("INSERT OR IGNORE INTO coins(coin_id,symbol,created_at) "
                     "VALUES(?,?,?)", (cid, f"D{i}", now))
        conn.execute(
            """INSERT INTO universe(run_id,run_date,coin_id,symbol,rank_by_mcap,
               market_cap,volume_24h,in_universe) VALUES(?,?,?,?,?,?,?,1)""",
            (last, "2026-06-22", cid, f"D{i}", i+11, 3e8, 1e7))
    for reason, cnt in funnel_fill:
        for i in range(cnt):
            cid = f"_dummy_{reason}_{i}"
            conn.execute("INSERT OR IGNORE INTO coins(coin_id,symbol,created_at) "
                         "VALUES(?,?,?)", (cid, f"F{i}", now))
            conn.execute(
                """INSERT INTO universe(run_id,run_date,coin_id,symbol,
                   in_universe,fail_reason) VALUES(?,?,?,?,0,?)""",
                (last, "2026-06-22", cid, f"F{i}", reason))
    # 10 العملات المسمّاة داخل الكون
    for i, (cid, sym, sec, rs, act) in enumerate(coins, 1):
        conn.execute(
            """INSERT INTO universe(run_id,run_date,coin_id,symbol,sector,
               rank_by_mcap,market_cap,volume_24h,age_days,in_universe)
               VALUES(?,?,?,?,?,?,?,?,?,1)""",
            (last, "2026-06-22", cid, sym, sec, i, 5e9, 8e7, 700))

    conn.commit()


def main():
    db = C.DATA_DIR / "_preview.db"
    if db.exists():
        db.unlink()
    conn = A.connect(db)
    A.init_schema(conn)
    seed(conn)
    conn.close()
    out = C.ROOT_DIR / "docs" / "index_preview.html"
    R.generate(db_path=db, out_path=out)
    db.unlink()
    print(f"المعاينة جاهزة: {out}")


if __name__ == "__main__":
    main()
