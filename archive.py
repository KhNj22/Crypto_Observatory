"""
Crypto Observatory V1 — archive.py
طبقة الأرشيف: الكتابة الذرّية + Idempotency + التحقّق + الـ hashing + استعلامات.

المبدأ: كل تشغيل = معاملة SQLite واحدة. إمّا تُكتب كاملة أو لا شيء (rollback).
التاريخ immutable: لا يُكتب فوق تشغيل مكتمل أبداً.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

import config as C


# ===========================================================================
# الاتصال + تهيئة المخطط
# ===========================================================================
def connect(db_path=None) -> sqlite3.Connection:
    """يفتح اتصالاً مع المفاتيح الأجنبية مفعّلة و WAL (ذرّية أفضل)."""
    path = str(db_path or C.DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """ينشئ الجداول (idempotent) ويضبط إصدار المخطط، ثم يزرع الأذرع."""
    sql = C.SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute(f"PRAGMA user_version = {C.SCHEMA_VERSION};")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(C.SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('created_at', ?)",
        (C.now_iso(),),
    )
    _seed_arms(conn)
    conn.commit()


def _seed_arms(conn: sqlite3.Connection) -> None:
    """يزرع أذرع الـ A/B والمعايير من config (لا يكرّر الموجود)."""
    now = C.now_ms()
    for code, name, weighting, sec, mkt, bench in C.ARM_DEFINITIONS:
        conn.execute(
            """INSERT OR IGNORE INTO arms
               (code, name, description, weighting_scheme,
                uses_sector, uses_market_filter, is_benchmark,
                initial_capital, active, created_at)
               VALUES (?,?,?,?,?,?,?,?,1,?)""",
            (code, name, name, weighting, sec, mkt, bench,
             C.INITIAL_CAPITAL, now),
        )


# ===========================================================================
# Idempotency + إدارة التشغيل
# ===========================================================================
def run_exists_ok(conn: sqlite3.Connection, run_date: str, run_type: str) -> bool:
    """هل يوجد تشغيل مكتمل (status='ok') لنفس المفتاح؟"""
    row = conn.execute(
        "SELECT 1 FROM runs WHERE run_date=? AND run_type=? AND status='ok'",
        (run_date, run_type),
    ).fetchone()
    return row is not None


def _purge_stale_run(conn: sqlite3.Connection, run_date: str, run_type: str) -> None:
    """يحذف أي تشغيل سابق غير مكتمل لنفس المفتاح (تنظيف قبل إعادة المحاولة).
    الحذف المتسلسل (ON DELETE CASCADE) يزيل كل صفوفه التابعة."""
    conn.execute(
        "DELETE FROM runs WHERE run_date=? AND run_type=? AND status<>'ok'",
        (run_date, run_type),
    )


# ===========================================================================
# الـ hashing للتكرارية العلمية
# ===========================================================================
def compute_content_hash(payload: dict[str, Any]) -> str:
    """sha256 لصورة canonical (مفاتيح مرتّبة) من حمولة اللقطة."""
    blob = json.dumps(payload, sort_keys=True, default=str,
                      ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ===========================================================================
# التحقّق من الاكتمال قبل الالتزام
# ===========================================================================
def _validate(run_type: str, payload: dict[str, Any]) -> None:
    """يرفع ValueError إذا كانت اللقطة ناقصة. لا نلتزم لقطة جزئية أبداً."""
    if "market_state" not in payload or not payload["market_state"]:
        raise ValueError("اللقطة ناقصة: market_state مطلوب في كل تشغيل.")

    ms = payload["market_state"]
    if ms.get("regime") not in (C.REGIME_RISK_ON, C.REGIME_RISK_OFF, C.REGIME_CAUTION):
        raise ValueError(f"regime غير صالح: {ms.get('regime')!r}")

    if run_type == "weekly":
        # التشغيل الأسبوعي يحمل الكون (survivorship) + مخرجات المرحلتين 2+3.
        # portfolio_state يُدرَج عند توفّره (المرحلة 5) لكنه ليس شرطاً قبلها.
        for key in ("universe", "sector_metrics", "selections"):
            if not payload.get(key):
                raise ValueError(f"التشغيل الأسبوعي ناقص: {key} مطلوب.")


# ===========================================================================
# دوال الكتابة لكل جدول (تُستدعى داخل المعاملة فقط)
# ===========================================================================
def _insert_coins(conn, coins: Iterable[dict]) -> None:
    now = C.now_ms()
    for c in coins:
        conn.execute(
            """INSERT INTO coins(coin_id,name,symbol,genesis_date,first_seen_date,created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(coin_id) DO UPDATE SET
                 name=excluded.name, symbol=excluded.symbol""",
            (c["coin_id"], c.get("name"), c.get("symbol"),
             c.get("genesis_date"), c.get("first_seen_date", C.today_utc()), now),
        )


def _insert_market_state(conn, run_id: int, ms: dict) -> None:
    cols = ("ts_utc","regime","regime_raw","btc_close","btc_sma200","btc_dist_pct",
            "hysteresis_pct","confirm_days","stablecoin_mcap","stablecoin_trend_30d",
            "fear_greed","fear_greed_label","breadth_pct_above_sma","advance_decline",
            "btc_dominance","total_mcap","total2_mcap","total3_mcap","eth_btc")
    vals = [run_id] + [ms.get(c) for c in cols]
    conn.execute(
        f"INSERT INTO market_state(run_id,{','.join(cols)}) "
        f"VALUES({','.join('?' * (len(cols)+1))})",
        vals,
    )


def _insert_universe(conn, run_id: int, run_date: str, rows: Sequence[dict]) -> None:
    conn.executemany(
        """INSERT INTO universe(run_id,run_date,coin_id,symbol,sector,rank_by_mcap,
                                market_cap,volume_24h,age_days,in_universe,fail_reason)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        [(run_id, run_date, r["coin_id"], r.get("symbol"), r.get("sector"),
          r.get("rank_by_mcap"), r.get("market_cap"), r.get("volume_24h"),
          r.get("age_days"), int(r.get("in_universe", 0)), r.get("fail_reason"))
         for r in rows],
    )


def _insert_portfolio_state(conn, run_id: int, rows: Sequence[dict]) -> None:
    conn.executemany(
        """INSERT INTO portfolio_state(run_id,arm_id,ts_utc,cash,invested_value,nav,
                                       n_positions,regime_at_run,turnover,fees_paid)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [(run_id, r["arm_id"], r.get("ts_utc", C.now_iso()), r["cash"],
          r["invested_value"], r["nav"], r.get("n_positions", 0),
          r.get("regime_at_run"), r.get("turnover", 0.0), r.get("fees_paid", 0.0))
         for r in rows],
    )


def _insert_trades(conn, run_id: int, rows: Sequence[dict]) -> None:
    if rows:
        C.assert_paper_only()   # الحاجز الصلب: لا صفقة بدون تأكيد أنها ورقية
    conn.executemany(
        """INSERT INTO trades(run_id,arm_id,coin_id,symbol,side,units,price,
                              gross_value,fee,slippage,net_value,reason,is_paper)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        [(run_id, r["arm_id"], r["coin_id"], r.get("symbol"), r["side"],
          r["units"], r["price"], r["gross_value"], r.get("fee", 0.0),
          r.get("slippage", 0.0), r["net_value"], r.get("reason"))
         for r in rows],
    )


# جداول إضافية تُكتب بنفس النمط (مختصرة هنا، تُستكمل في المراحل اللاحقة)
_GENERIC_INSERTS = {
    "coin_metrics": ("run_id", "coin_id"),
    "sector_metrics": ("run_id", "sector"),
    "selections": ("run_id", "coin_id"),
    "holdings": ("run_id", "arm_id", "coin_id"),
}


def _insert_generic(conn, table: str, run_id: int, rows: Sequence[dict]) -> None:
    """إدراج عام لجداول اللقطة البسيطة (يبني الأعمدة من مفاتيح أول صف)."""
    if not rows:
        return
    sample = {k: v for k, v in rows[0].items() if k != "run_id"}
    cols = list(sample.keys())
    placeholders = ",".join("?" * (len(cols) + 1))
    conn.executemany(
        f"INSERT INTO {table}(run_id,{','.join(cols)}) VALUES({placeholders})",
        [tuple([run_id] + [r.get(c) for c in cols]) for r in rows],
    )


# ===========================================================================
# نقطة الدخول الذرّية الوحيدة: snapshot()
# ===========================================================================
def snapshot(run_date: str, run_type: str, payload: dict[str, Any],
             db_path=None, force: bool = False) -> dict[str, Any]:
    """
    يكتب لقطة تشغيل كاملة بشكل ذرّي و idempotent.

    payload يمكن أن يحوي المفاتيح:
      coins, market_state(مطلوب), universe, coin_metrics, sector_metrics,
      selections, portfolio_state, holdings, trades

    العائد: dict فيه run_id, status, content_hash, skipped.
    السلوك:
      - تشغيل مكتمل موجود ⇒ يُتخطّى (إلا force=True).
      - أي خطأ أثناء الكتابة ⇒ rollback كامل + تسجيل الفشل (لا لقطة جزئية).
    """
    conn = connect(db_path)
    try:
        # 1) Idempotency
        if run_exists_ok(conn, run_date, run_type) and not force:
            return {"run_id": None, "status": "skipped",
                    "content_hash": None, "skipped": True}

        # 2) تحقّق الاكتمال قبل أي كتابة
        _validate(run_type, payload)
        content_hash = compute_content_hash(payload)

        run_id = None
        try:
            with conn:  # معاملة واحدة: COMMIT عند النجاح، ROLLBACK عند أي استثناء
                _purge_stale_run(conn, run_date, run_type)
                cur = conn.execute(
                    """INSERT INTO runs(run_date,run_type,started_at,status,
                                        data_complete,content_hash,schema_version)
                       VALUES(?,?,?, 'running', 0, ?, ?)""",
                    (run_date, run_type, C.now_ms(), content_hash, C.SCHEMA_VERSION),
                )
                run_id = cur.lastrowid

                if payload.get("coins"):
                    _insert_coins(conn, payload["coins"])
                _insert_market_state(conn, run_id, payload["market_state"])
                if payload.get("universe"):
                    _insert_universe(conn, run_id, run_date, payload["universe"])
                for tbl in _GENERIC_INSERTS:
                    if payload.get(tbl):
                        _insert_generic(conn, tbl, run_id, payload[tbl])
                if payload.get("portfolio_state"):
                    _insert_portfolio_state(conn, run_id, payload["portfolio_state"])
                if payload.get("trades"):
                    _insert_trades(conn, run_id, payload["trades"])

                # 3) ختم النجاح داخل نفس المعاملة
                conn.execute(
                    "UPDATE runs SET completed_at=?, status='ok', data_complete=1 "
                    "WHERE run_id=?",
                    (C.now_ms(), run_id),
                )
            return {"run_id": run_id, "status": "ok",
                    "content_hash": content_hash, "skipped": False}

        except Exception as exc:
            # المعاملة عُكِست تلقائياً. نسجّل الفشل في صف منفصل (خارج المعاملة).
            conn.execute(
                """INSERT INTO runs(run_date,run_type,started_at,completed_at,
                                    status,data_complete,schema_version,notes)
                   VALUES(?,?,?,?, 'failed', 0, ?, ?)""",
                (run_date, run_type, C.now_ms(), C.now_ms(),
                 C.SCHEMA_VERSION, f"{type(exc).__name__}: {exc}"),
            )
            conn.commit()
            raise
    finally:
        conn.close()


# ===========================================================================
# استعلامات مساعدة
# ===========================================================================
def latest_run(conn, run_type: str | None = None) -> sqlite3.Row | None:
    q = "SELECT * FROM runs WHERE status='ok'"
    args: list = []
    if run_type:
        q += " AND run_type=?"
        args.append(run_type)
    q += " ORDER BY run_date DESC, run_id DESC LIMIT 1"
    return conn.execute(q, args).fetchone()


def nav_series(conn, arm_code: str) -> list[sqlite3.Row]:
    """سلسلة الـ NAV لذراع عبر الزمن (أساس حساب KPI)."""
    return conn.execute(
        """SELECT r.run_date, p.nav, p.cash, p.invested_value, p.regime_at_run
           FROM portfolio_state p
           JOIN runs r ON r.run_id = p.run_id
           JOIN arms a ON a.arm_id = p.arm_id
           WHERE a.code=? AND r.status='ok'
           ORDER BY r.run_date""",
        (arm_code,),
    ).fetchall()


def get_universe(conn, run_date: str, in_universe_only: bool = True) -> list[sqlite3.Row]:
    """عضوية الكون نقطة-زمنية لتاريخ محدّد."""
    q = "SELECT * FROM universe WHERE run_date=?"
    if in_universe_only:
        q += " AND in_universe=1"
    q += " ORDER BY rank_by_mcap"
    return conn.execute(q, (run_date,)).fetchall()


def arm_id_by_code(conn, code: str) -> int | None:
    row = conn.execute("SELECT arm_id FROM arms WHERE code=?", (code,)).fetchone()
    return row["arm_id"] if row else None


def table_counts(conn) -> dict[str, int]:
    """عدد الصفوف في كل جدول (لفحص الصحّة)."""
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in tables}
