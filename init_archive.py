"""
Crypto Observatory V1 — init_archive.py
تهيئة الأرشيف + فحص ذاتي يثبت: الكتابة الذرّية، الـ Idempotency، والـ rollback.

شغّل:  python init_archive.py
هذا الفحص هو "المراجعة الدقيقة قبل الإرسال" — يبرهن سلامة الأساس عملياً.
"""

from __future__ import annotations

import config as C
import archive as A


def _hr(title: str) -> None:
    print("\n" + "─" * 70)
    print(f"  {title}")
    print("─" * 70)


def main() -> None:
    # نستخدم قاعدة اختبار منفصلة حتى لا نلوّث الأرشيف الحقيقي.
    test_db = C.DATA_DIR / "_selftest.db"
    if test_db.exists():
        test_db.unlink()

    # ---------------------------------------------------------------
    _hr("1) تهيئة المخطط + زرع الأذرع")
    conn = A.connect(test_db)
    A.init_schema(conn)

    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    n_arms = conn.execute("SELECT COUNT(*) FROM arms").fetchone()[0]
    n_bench = conn.execute("SELECT COUNT(*) FROM arms WHERE is_benchmark=1").fetchone()[0]
    print(f"إصدار المخطط (user_version) = {ver}")
    print(f"عدد الأذرع المزروعة = {n_arms}  (منها معايير = {n_bench})")
    counts = A.table_counts(conn)
    print(f"عدد الجداول = {len(counts)}")
    print("الجداول:", ", ".join(counts.keys()))
    assert ver == C.SCHEMA_VERSION
    assert n_arms == len(C.ARM_DEFINITIONS)
    conn.close()

    # ---------------------------------------------------------------
    _hr("2) كتابة لقطة أسبوعية صحيحة (اختبار الذرّية + النجاح)")
    conn = A.connect(test_db)
    arm_C = A.arm_id_by_code(conn, "C")
    arm_D = A.arm_id_by_code(conn, "D")
    conn.close()

    payload = {
        "coins": [
            {"coin_id": "bitcoin",  "name": "Bitcoin",  "symbol": "BTC"},
            {"coin_id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
            {"coin_id": "solana",   "name": "Solana",   "symbol": "SOL"},
        ],
        "market_state": {
            "ts_utc": C.now_iso(), "regime": C.REGIME_RISK_ON,
            "regime_raw": C.REGIME_RISK_ON, "btc_close": 64000.0,
            "btc_sma200": 58000.0, "btc_dist_pct": 0.1034,
            "hysteresis_pct": C.HYSTERESIS_PCT, "confirm_days": C.CONFIRM_DAYS,
            "stablecoin_mcap": 1.62e11, "stablecoin_trend_30d": 0.018,
            "fear_greed": 72, "fear_greed_label": "Greed",
            "breadth_pct_above_sma": 0.61,
        },
        "universe": [
            {"coin_id": "bitcoin",  "symbol": "BTC", "sector": "L1",
             "rank_by_mcap": 1, "market_cap": 1.26e12, "volume_24h": 3.1e10,
             "age_days": 5000, "in_universe": 1},
            {"coin_id": "ethereum", "symbol": "ETH", "sector": "L1",
             "rank_by_mcap": 2, "market_cap": 4.1e11, "volume_24h": 1.5e10,
             "age_days": 3500, "in_universe": 1},
            {"coin_id": "solana",   "symbol": "SOL", "sector": "L1",
             "rank_by_mcap": 5, "market_cap": 7.2e10, "volume_24h": 3.0e9,
             "age_days": 1600, "in_universe": 1},
        ],
        "sector_metrics": [
            {"sector": "L1", "rs_30d": 0.05, "rs_90d": 0.08, "momentum": 0.08,
             "breadth": 0.6, "new_high_ratio": 0.3, "mcap_share": 0.7,
             "sector_score": 100.0, "sector_rank": 1},
        ],
        "selections": [
            {"coin_id": "bitcoin", "symbol": "BTC", "sector": "L1",
             "composite_rank": 1, "action": "enter", "in_top": 1, "tier": 1},
            {"coin_id": "ethereum", "symbol": "ETH", "sector": "L1",
             "composite_rank": 2, "action": "enter", "in_top": 1, "tier": 1},
        ],
        "portfolio_state": [
            {"arm_id": arm_C, "cash": 4000.0, "invested_value": 6200.0,
             "nav": 10200.0, "n_positions": 3, "regime_at_run": C.REGIME_RISK_ON,
             "turnover": 0.25, "fees_paid": 6.2},
            {"arm_id": arm_D, "cash": 0.0, "invested_value": 10150.0,
             "nav": 10150.0, "n_positions": 2, "regime_at_run": C.REGIME_RISK_ON},
        ],
        "trades": [
            {"arm_id": arm_C, "coin_id": "solana", "symbol": "SOL", "side": "buy",
             "units": 20.0, "price": 150.0, "gross_value": 3000.0,
             "fee": 3.0, "slippage": 4.5, "net_value": 3007.5, "reason": "enter"},
        ],
    }

    res = A.snapshot("2026-06-22", "weekly", payload, db_path=test_db)
    print(f"نتيجة الكتابة: status={res['status']}  run_id={res['run_id']}")
    print(f"content_hash = {res['content_hash'][:16]}…")
    assert res["status"] == "ok"

    conn = A.connect(test_db)
    counts = A.table_counts(conn)
    print(f"صفوف بعد اللقطة: runs={counts['runs']}, universe={counts['universe']}, "
          f"portfolio_state={counts['portfolio_state']}, trades={counts['trades']}")
    nav = A.nav_series(conn, "C")
    print(f"NAV لذراع C: {[(r['run_date'], r['nav']) for r in nav]}")
    assert counts["runs"] == 1 and counts["trades"] == 1
    conn.close()

    # ---------------------------------------------------------------
    _hr("3) Idempotency — إعادة نفس التشغيل يجب أن تُتخطّى")
    res2 = A.snapshot("2026-06-22", "weekly", payload, db_path=test_db)
    print(f"النتيجة الثانية: status={res2['status']}  skipped={res2['skipped']}")
    conn = A.connect(test_db)
    n_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    print(f"عدد التشغيلات لم يتغيّر = {n_runs}  | الصفقات لم تتكرّر = {n_trades}")
    assert res2["status"] == "skipped"
    assert n_runs == 1 and n_trades == 1   # لم يُكتب شيء جديد
    conn.close()

    # ---------------------------------------------------------------
    _hr("4) Rollback — لقطة ناقصة يجب ألا تُكتب جزئياً")
    bad_payload = {   # لا يحوي market_state ⇒ يجب أن يفشل التحقّق
        "universe": [{"coin_id": "bitcoin", "in_universe": 1}],
    }
    threw = False
    try:
        A.snapshot("2026-06-29", "weekly", bad_payload, db_path=test_db)
    except ValueError as e:
        threw = True
        print(f"رُفِض كما هو متوقّع: {e}")

    conn = A.connect(test_db)
    ok_runs = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE status='ok'").fetchone()[0]
    failed_runs = conn.execute(
        "SELECT run_date,notes FROM runs WHERE status='failed'").fetchall()
    # لا يجب أن توجد أي صفوف universe للتاريخ الفاشل (rollback تام)
    leaked = conn.execute(
        "SELECT COUNT(*) FROM universe WHERE run_date='2026-06-29'").fetchone()[0]
    print(f"تشغيلات ناجحة = {ok_runs} (ثابتة)  | صفوف مسرّبة من الفاشل = {leaked}")
    if failed_runs:
        print(f"سُجِّل الفشل: {failed_runs[0]['run_date']} → {failed_runs[0]['notes']}")
    assert threw and ok_runs == 1 and leaked == 0
    conn.close()

    # ---------------------------------------------------------------
    _hr("5) الحاجز الصلب للورق")
    print(f"LIVE_TRADING = {C.LIVE_TRADING}")
    C.assert_paper_only()
    print("assert_paper_only() مرّ ✓ — لا تداول حقيقي ممكن.")

    # ---------------------------------------------------------------
    _hr("النتيجة")
    print("جميع الفحوص نجحت ✓")
    print("الأساس سليم: ذرّي + idempotent + يرفض اللقطات الناقصة + ورقي بحت.")
    test_db.unlink()   # تنظيف قاعدة الاختبار


if __name__ == "__main__":
    main()
