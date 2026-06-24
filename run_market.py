"""
Crypto Observatory V1 — run_market.py
مُنسّق Market Observatory (المرحلة 1). نقطة التشغيل اليومي.

التدفّق:  اقرأ الحالة السابقة ← اجلب البيانات ← احسب الحالة ←
          اكتب لقطة ذرّية ← اطبع تقريراً ← نبّه عند تغيّر الحالة.

شغّل:  python run_market.py
"""

from __future__ import annotations

import archive as A
import config as C
import data_sources as DS
import market_observatory as MO
import telegram_bot as TG


def _prev_regime(conn) -> str | None:
    """يقرأ آخر حالة مُلتزَمة من الأرشيف (مبدأ: اقرأ حالتك السابقة)."""
    row = conn.execute(
        """SELECT m.regime FROM market_state m
           JOIN runs r ON r.run_id = m.run_id
           WHERE r.status='ok' ORDER BY r.run_date DESC, r.run_id DESC LIMIT 1"""
    ).fetchone()
    return row["regime"] if row else None


def _format_report(ms: dict) -> str:
    arrow = "🟢" if ms["regime"] == C.REGIME_RISK_ON else (
            "🔴" if ms["regime"] == C.REGIME_RISK_OFF else "🟡")
    lines = [
        f"*Crypto Observatory* — {C.today_utc()} (UTC)",
        f"{arrow} *Regime:* `{ms['regime']}`"
        + ("  ⚠️ تغيّر!" if ms.get("_changed") else ""),
        f"BTC: `{ms['btc_close']:,.0f}`  |  SMA200: `{ms['btc_sma200']:,.0f}`"
        f"  ({ms['btc_dist_pct']*100:+.1f}%)",
    ]
    if ms.get("fear_greed") is not None:
        lines.append(f"F&G: `{ms['fear_greed']}` ({ms.get('fear_greed_label','')})")
    if ms.get("stablecoin_trend_30d") is not None:
        lines.append(f"Stablecoin 30d: `{ms['stablecoin_trend_30d']*100:+.1f}%`")
    return "\n".join(lines)


def main() -> dict:
    # 1) الحالة السابقة من الأرشيف
    conn = A.connect()
    A.init_schema(conn)               # idempotent — يضمن وجود الجداول
    prev = _prev_regime(conn)
    conn.close()

    # 2) جلب البيانات (تتصل بالإنترنت — تعمل على جهازك)
    btc = DS.fetch_binance_klines("BTCUSDT", "1d", limit=400)
    try:
        eth_btc = DS.fetch_last_close("ETHBTC")
    except Exception as e:
        print(f"[تحذير] تعذّر جلب ETHBTC: {e}"); eth_btc = None
    try:
        stable = DS.fetch_stablecoin_mcap()
    except Exception as e:
        print(f"[تحذير] تعذّر جلب Stablecoin mcap: {e}"); stable = None
    try:
        fng = DS.fetch_fear_greed()
    except Exception as e:
        print(f"[تحذير] تعذّر جلب Fear & Greed: {e}"); fng = None

    # 3) حساب الحالة
    ms = MO.compute_market_state(btc, stablecoin=stable, fear_greed=fng,
                                 eth_btc=eth_btc, prev_regime=prev)

    # 4) كتابة لقطة ذرّية (نوع: daily)
    payload = {
        "coins": [{"coin_id": "bitcoin", "name": "Bitcoin", "symbol": "BTC"}],
        "market_state": {k: v for k, v in ms.items() if not k.startswith("_")},
    }
    res = A.snapshot(C.today_utc(), "daily", payload)

    # 5) تقرير + تنبيه
    report = _format_report(ms)
    print("\n" + report + "\n")
    print(f"[أرشيف] {res['status']}  run_id={res.get('run_id')}")

    if ms.get("_changed"):
        TG.tg_send("⚠️ *تغيّر حالة السوق*\n\n" + report)
    else:
        # في التشغيل اليومي الصامت، نرسل فقط عند التغيّر (لا ضوضاء)
        if TG.is_configured():
            print("[تيليجرام] لا تغيّر — لم تُرسل رسالة (تصميم صامت).")

    return res


if __name__ == "__main__":
    main()
