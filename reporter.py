"""
Crypto Observatory V1 — reporter.py
محرّك صياغة واجهة الويب: يقرأ الأرشيف ويُنتج docs/index.html ثابتاً self-contained.

التصميم: طرفية كوانت (Quant Terminal) — Dark premium، Glassmorphism، monospace للأرقام.
يقرأ الجداول دفاعياً: يعرض ما هو موجود ويُظهر حالات "بانتظار المرحلة X" بأناقة.

شغّل:  python reporter.py        (يكتب docs/index.html من الأرشيف الفعلي)
يُدمَج تلقائياً في daily.yml بعد run_market.py.
"""

from __future__ import annotations

import html
import json

import config as C
import archive as A


# ===========================================================================
# 1) قراءة البيانات من الأرشيف (دفاعي — قد تكون بعض الجداول فارغة)
# ===========================================================================
def _latest_run_id(conn, run_type: str | None = None):
    q = "SELECT run_id FROM runs WHERE status='ok'"
    a: list = []
    if run_type:
        q += " AND run_type=?"; a.append(run_type)
    q += " ORDER BY run_date DESC, run_id DESC LIMIT 1"
    row = conn.execute(q, a).fetchone()
    return row["run_id"] if row else None


def load_dashboard_data(conn) -> dict:
    data: dict = {"market": None, "sectors": [], "top": [], "nav": None,
                  "kpis": None, "funnel": None, "phases": {}}

    # --- Market state (المرحلة 1 — حيّ) ---
    rid_any = _latest_run_id(conn)
    if rid_any:
        m = conn.execute("SELECT * FROM market_state WHERE run_id=?",
                         (rid_any,)).fetchone()
        r = conn.execute("SELECT run_date FROM runs WHERE run_id=?",
                         (rid_any,)).fetchone()
        if m:
            md = dict(m) | {"run_date": r["run_date"]}
            # وقت المزامنة الدقيق HH:MM UTC من ts_utc (تحليل متين + احتياطي)
            ts = md.get("ts_utc") or ""
            try:
                import datetime as _dt
                md["sync_time"] = _dt.datetime.fromisoformat(ts).strftime("%H:%M")
            except (ValueError, TypeError):
                md["sync_time"] = ts[11:16] if len(ts) >= 16 else "—"
            data["market"] = md

    # --- آخر تشغيلين أسبوعيين (للقطاعات/الدوران) ---
    w_rows = conn.execute(
        "SELECT run_id FROM runs WHERE run_type='weekly' AND status='ok' "
        "ORDER BY run_date DESC, run_id DESC LIMIT 2").fetchall()
    rid_w = w_rows[0]["run_id"] if w_rows else None
    rid_w_prev = w_rows[1]["run_id"] if len(w_rows) > 1 else None

    # --- Sector heatmap + دوران (المرحلة 2) ---
    if rid_w:
        rows = conn.execute(
            "SELECT * FROM sector_metrics WHERE run_id=? ORDER BY sector_rank, "
            "sector_score DESC", (rid_w,)).fetchall()
        prev_rank = {}
        if rid_w_prev:
            prev_rank = {x["sector"]: x["sector_rank"] for x in conn.execute(
                "SELECT sector, sector_rank FROM sector_metrics WHERE run_id=?",
                (rid_w_prev,)).fetchall()}
        secs = []
        for x in rows:
            d = dict(x)
            pr = prev_rank.get(d["sector"])
            d["rank_delta"] = (pr - d["sector_rank"]) if (pr and d["sector_rank"]) else None
            secs.append(d)
        data["sectors"] = secs

        # --- قُمع الكون (من جدول universe) ---
        tot = conn.execute("SELECT COUNT(*) c FROM universe WHERE run_id=?",
                           (rid_w,)).fetchone()["c"]
        if tot:
            passed = conn.execute(
                "SELECT COUNT(*) c FROM universe WHERE run_id=? AND in_universe=1",
                (rid_w,)).fetchone()["c"]
            br = {r2["fail_reason"]: r2["c"] for r2 in conn.execute(
                "SELECT fail_reason, COUNT(*) c FROM universe WHERE run_id=? "
                "AND in_universe=0 GROUP BY fail_reason", (rid_w,)).fetchall()}
            data["funnel"] = {"scanned": tot, "passed": passed, "reasons": br}

    # --- Top selections (المرحلة 3) ---
    if rid_w:
        rows = conn.execute(
            """SELECT s.coin_id, s.symbol, s.sector, s.composite_rank, s.action,
                      s.in_top, cm.rs_90d, cm.trend_up, cm.atr_pct
               FROM selections s
               LEFT JOIN coin_metrics cm
                 ON cm.run_id=s.run_id AND cm.coin_id=s.coin_id
               WHERE s.run_id=? AND s.in_top=1
               ORDER BY s.composite_rank LIMIT 20""", (rid_w,)).fetchall()
        data["top"] = [dict(x) for x in rows]

    # --- NAV series (المراحل 3-5) — الاستراتيجية C مقابل Golden Core D ---
    nav_c = A.nav_series(conn, "C")
    nav_d = A.nav_series(conn, "D")
    if nav_c:
        labels = [r["run_date"] for r in nav_c]
        series_c = [round(r["nav"], 2) for r in nav_c]
        d_by_date = {r["run_date"]: round(r["nav"], 2) for r in nav_d}
        series_d = [d_by_date.get(lbl) for lbl in labels]
        data["nav"] = {"labels": labels, "strategy": series_c, "golden": series_d}
        init = series_c[0] if series_c else C.INITIAL_CAPITAL
        cur = series_c[-1]
        peak = init; mdd = 0.0
        for v in series_c:
            peak = max(peak, v); mdd = min(mdd, v / peak - 1.0)
        vs_gc = (cur / series_d[-1] - 1.0) if (series_d and series_d[-1]) else None
        data["kpis"] = {"nav": cur, "total_return": cur / init - 1.0,
                        "max_drawdown": mdd, "vs_golden": vs_gc}

    data["phases"] = {
        "regime": data["market"] is not None,
        "sectors": bool(data["sectors"]),
        "ranking": bool(data["top"]),
        "portfolio": data["nav"] is not None,
    }
    return data


# ===========================================================================
# 2) أدوات التلوين
# ===========================================================================
def _heat_style(score, rs) -> str:
    """خلفية المربع: أخضر إن RS موجب، أحمر إن سالب؛ الشدّة من sector_score."""
    s = max(0.0, min(1.0, (score or 0) / 100.0))
    a = 0.10 + 0.55 * s
    rgb = "0,230,118" if (rs or 0) >= 0 else "255,77,77"
    return (f"background:linear-gradient(135deg,rgba({rgb},{a:.2f}),"
            f"rgba({rgb},{a*0.35:.2f}));"
            f"border-color:rgba({rgb},{0.18+0.5*s:.2f});")


def _pct(x, digits=1, sign=True) -> str:
    if x is None:
        return "—"
    s = f"{x*100:+.{digits}f}%" if sign else f"{x*100:.{digits}f}%"
    return s


def _num(x, digits=0) -> str:
    if x is None:
        return "—"
    return f"{x:,.{digits}f}"


# ===========================================================================
# 3) CSS — طرفية كوانت، Dark premium (مكتوب يدوياً، لا Tailwind)
# ===========================================================================
_CSS = """
:root{
  --bg:#0b0e14; --bg2:#0e1219; --panel:rgba(255,255,255,.035);
  --panel-brd:rgba(255,255,255,.07); --elev:#11151f;
  --tx:#e6e9f0; --tx-mut:#7b8294; --tx-faint:#4a4f5c;
  --on:#00e676; --off:#ff4d4d; --caution:#ffb020; --bench:#4d9fff;
  --r:16px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:radial-gradient(1200px 700px at 70% -10%,#141b29 0%,var(--bg) 55%) fixed,var(--bg);
  color:var(--tx); font-family:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif;
  line-height:1.5; -webkit-font-smoothing:antialiased; padding:28px 18px 60px;
}
.mono{font-family:'JetBrains Mono','SF Mono',ui-monospace,monospace;
  font-feature-settings:'tnum' 1; letter-spacing:-.01em}
.wrap{max-width:1180px; margin:0 auto}
.eyebrow{font-size:11px; letter-spacing:.22em; text-transform:uppercase;
  color:var(--tx-faint); font-weight:600}
.muted{color:var(--tx-mut)}
.panel{background:var(--panel); border:1px solid var(--panel-brd);
  border-radius:var(--r); backdrop-filter:blur(14px) saturate(120%);
  -webkit-backdrop-filter:blur(14px) saturate(120%)}
.up{color:var(--on)} .down{color:var(--off)}

/* ---- Regime header (الـ signature) ---- */
.hero{position:relative; padding:30px 30px 26px; margin-bottom:18px; overflow:hidden}
.hero::before{content:''; position:absolute; inset:0;
  background:radial-gradient(700px 220px at 18% 0%,var(--glow,transparent) 0%,transparent 70%);
  pointer-events:none}
.hero-top{display:flex; justify-content:space-between; align-items:flex-start;
  gap:20px; flex-wrap:wrap; position:relative}
.regime-badge{display:inline-flex; align-items:center; gap:12px; margin-top:8px}
.dot{width:13px; height:13px; border-radius:50%; background:var(--rc);
  box-shadow:0 0 14px 3px var(--rc); animation:breathe 3.2s ease-in-out infinite}
@keyframes breathe{0%,100%{opacity:.55; transform:scale(.92)}50%{opacity:1; transform:scale(1.12)}}
.regime-word{font-size:42px; font-weight:700; line-height:1; color:var(--rc);
  text-shadow:0 0 26px var(--glow)}
.regime-sub{font-size:12px; color:var(--tx-mut); margin-top:6px}
.pulse{height:2px; margin-top:20px; border-radius:2px; position:relative;
  background:linear-gradient(90deg,transparent,var(--rc),transparent); opacity:.5}

/* ---- KPI strip ---- */
.kpis{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:18px}
.kpi{padding:16px 18px}
.kpi .label{font-size:11px; color:var(--tx-mut); letter-spacing:.04em}
.kpi .val{font-size:23px; font-weight:600; margin-top:6px}
.kpi .pending{font-size:13px; color:var(--tx-faint); margin-top:8px}

/* ---- main grid ---- */
.grid{display:grid; grid-template-columns:1.5fr 1fr; gap:16px; margin-bottom:18px}
.card{padding:20px 22px}
.card h3{font-size:13px; font-weight:600; letter-spacing:.02em; margin-bottom:2px}
.card .sub{font-size:11px; color:var(--tx-faint); margin-bottom:16px}
.chart-box{position:relative; height:300px}

/* ---- heatmap ---- */
.funnel{display:flex; gap:7px; flex-wrap:wrap; margin-bottom:14px}
.fchip{font-size:10.5px; padding:4px 9px; border-radius:7px; color:var(--tx-mut);
  background:rgba(255,255,255,.04); border:1px solid var(--panel-brd)}
.fchip b{color:var(--tx); font-weight:700}
.fchip.live{color:var(--on); border-color:rgba(0,230,118,.3)}
.fchip.live b{color:var(--on)}
.heat{display:grid; grid-template-columns:repeat(3,1fr); gap:9px}
.tile{border:1px solid; border-radius:11px; padding:13px 12px; min-height:78px;
  display:flex; flex-direction:column; justify-content:space-between;
  transition:transform .15s ease, box-shadow .15s ease}
.tile:hover{transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,.35)}
.tile .name{font-size:13px; font-weight:600}
.tile .rs{font-size:18px; font-weight:700}
.tile .sc{font-size:10px; color:var(--tx-mut)}

/* ---- table ---- */
.tbl{width:100%; border-collapse:collapse}
.tbl th{text-align:left; font-size:10.5px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--tx-faint); font-weight:600; padding:10px 12px; border-bottom:1px solid var(--panel-brd)}
.tbl td{padding:13px 12px; border-bottom:1px solid rgba(255,255,255,.04); font-size:14px}
.tbl tr:last-child td{border-bottom:none}
.tbl tr.hot td{background:linear-gradient(90deg,rgba(0,230,118,.05),transparent)}
.rank{display:inline-flex; width:24px; height:24px; align-items:center;
  justify-content:center; border-radius:7px; font-size:12px; font-weight:700;
  background:rgba(255,255,255,.06)}
.rank.top{background:rgba(0,230,118,.16); color:var(--on)}
.sym{font-weight:700; font-size:15px}
.tag{font-size:10px; padding:3px 8px; border-radius:6px; background:rgba(77,159,255,.12);
  color:var(--bench); font-weight:600; letter-spacing:.03em}
.pill{font-size:11px; font-weight:700; padding:3px 9px; border-radius:20px}
.pill.enter{background:rgba(0,230,118,.14); color:var(--on)}
.pill.hold{background:rgba(255,255,255,.07); color:var(--tx-mut)}
.pill.exit{background:rgba(255,77,77,.14); color:var(--off)}
.arrow{font-size:12px; margin-right:4px}

/* ---- pending state ---- */
.pending-box{padding:38px 22px; text-align:center; border:1px dashed var(--panel-brd);
  border-radius:12px; color:var(--tx-mut)}
.pending-box .ph{font-size:11px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--caution); font-weight:600; margin-bottom:8px}

/* ---- footer ---- */
.foot{display:flex; justify-content:space-between; align-items:center; gap:16px;
  flex-wrap:wrap; padding:18px 22px; font-size:11px; color:var(--tx-faint)}
.phase-tags{display:flex; gap:8px; flex-wrap:wrap}
.ptag{font-size:10px; padding:4px 10px; border-radius:6px; border:1px solid var(--panel-brd)}
.ptag.live{color:var(--on); border-color:rgba(0,230,118,.3)}
.ptag.pend{color:var(--tx-faint)}

@media (max-width:860px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  .grid{grid-template-columns:1fr}
  .regime-word{font-size:34px}
}
@media (prefers-reduced-motion:reduce){
  .dot{animation:none} *{transition:none!important}
}
"""

# JS لإنشاء رسم الأداء (Chart.js) — البيانات تُحقن عبر placeholder
_JS = """
const NAV = __NAV_JSON__;
if (NAV && window.Chart) {
  const ctx = document.getElementById('navChart').getContext('2d');
  const g = ctx.createLinearGradient(0,0,0,300);
  g.addColorStop(0,'rgba(0,230,118,.28)'); g.addColorStop(1,'rgba(0,230,118,0)');
  new Chart(ctx,{type:'line',
    data:{labels:NAV.labels,datasets:[
      {label:'Strategy (C)',data:NAV.strategy,borderColor:'#00e676',
       backgroundColor:g,fill:true,borderWidth:2,tension:.32,pointRadius:0,
       pointHoverRadius:4},
      {label:'Golden Core',data:NAV.golden,borderColor:'#4d9fff',
       borderDash:[5,4],fill:false,borderWidth:1.6,tension:.32,pointRadius:0,
       pointHoverRadius:4}
    ]},
    options:{responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{labels:{color:'#7b8294',font:{size:11},usePointStyle:true,
        pointStyleWidth:8,boxHeight:6}},
        tooltip:{backgroundColor:'#11151f',borderColor:'rgba(255,255,255,.1)',
          borderWidth:1,titleColor:'#e6e9f0',bodyColor:'#b8bdc9',padding:10,
          callbacks:{label:c=>` ${c.dataset.label}: ${Number(c.parsed.y).toLocaleString()}`}}},
      scales:{
        x:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#4a4f5c',font:{size:10},
           maxTicksLimit:8}},
        y:{grid:{color:'rgba(255,255,255,.04)'},ticks:{color:'#4a4f5c',font:{size:10},
           callback:v=>'$'+Number(v).toLocaleString()}}}}});
}
"""


# ===========================================================================
# 4) صياغة أقسام HTML
# ===========================================================================
def _render_hero(m: dict | None) -> str:
    if not m:
        return ('<div class="hero panel" style="--rc:#7b8294;--glow:transparent">'
                '<div class="eyebrow">Crypto Observatory</div>'
                '<div class="regime-word" style="font-size:30px">بانتظار أول تشغيل</div>'
                '<div class="regime-sub">شغّل run_market.py لتوليد أول حالة سوق.</div>'
                '</div>')
    regime = m.get("regime", "")
    rc = {"risk_on": "var(--on)", "risk_off": "var(--off)",
          "caution": "var(--caution)"}.get(regime, "var(--tx-mut)")
    label = {"risk_on": "RISK-ON", "risk_off": "RISK-OFF",
             "caution": "CAUTION"}.get(regime, regime.upper())
    dist = m.get("btc_dist_pct") or 0
    glow_a = max(0.12, min(0.55, 0.12 + abs(dist) * 1.6))
    glow = ("rgba(0,230,118," if regime == "risk_on" else
            "rgba(255,77,77,") + f"{glow_a:.2f})"
    fng = m.get("fear_greed")
    fng_txt = (f'{fng} · {html.escape(str(m.get("fear_greed_label") or ""))}'
               if fng is not None else "—")
    stbl = _pct(m.get("stablecoin_trend_30d"))
    # اتّساع السوق (المرحلة 2): % الكون فوق متوسطه — إشارة صحّة داخلية
    breadth = m.get("breadth_pct_above_sma")
    breadth_html = ""
    if breadth is not None:
        bup = breadth >= 0.5
        breadth_html = (
            '<div><span class="muted" style="font-size:11px">Market Breadth</span><br>'
            f'<span style="font-size:15px" class="{"up" if bup else "down"}">'
            f'{breadth*100:.0f}% فوق SMA</span></div>')
    return f"""
<div class="hero panel" style="--rc:{rc};--glow:{glow}">
  <div class="hero-top">
    <div>
      <div class="eyebrow">Crypto Observatory · المرصد</div>
      <div class="regime-badge"><span class="dot" style="--rc:{rc}"></span>
        <span class="regime-word">{label}</span></div>
      <div class="regime-sub">حالة السوق محسوبة من BTC مقابل SMA200 (مع hysteresis وتأكيد)</div>
    </div>
    <div style="text-align:right">
      <div class="eyebrow">BTC / USDT</div>
      <div class="mono" style="font-size:30px;font-weight:600">${_num(m.get("btc_close"))}</div>
      <div class="mono muted" style="font-size:12px;margin-top:4px">
        SMA200 ${_num(m.get("btc_sma200"))} ·
        <span class="{'up' if dist>=0 else 'down'}">{_pct(dist)}</span></div>
    </div>
  </div>
  <div style="display:flex;gap:26px;margin-top:18px;flex-wrap:wrap" class="mono">
    <div><span class="muted" style="font-size:11px">Fear &amp; Greed</span><br>
      <span style="font-size:15px">{fng_txt}</span></div>
    <div><span class="muted" style="font-size:11px">Stablecoin 30d</span><br>
      <span style="font-size:15px" class="{'up' if (m.get('stablecoin_trend_30d') or 0)>=0 else 'down'}">{stbl}</span></div>
    {breadth_html}
    <div><span class="muted" style="font-size:11px">Last Synced (UTC)</span><br>
      <span style="font-size:15px">{html.escape(str(m.get('run_date') or ''))} ·
      {html.escape(str(m.get('sync_time') or '—'))}</span></div>
  </div>
  <div class="pulse" style="--rc:{rc}"></div>
</div>"""


def _render_kpis(k: dict | None) -> str:
    def card(label, val_html, pending=False):
        inner = (f'<div class="pending">{val_html}</div>' if pending
                 else f'<div class="val mono">{val_html}</div>')
        return f'<div class="kpi panel"><div class="label">{label}</div>{inner}</div>'
    if not k:
        p = "بانتظار المحفظة (مرحلة 5)"
        return ('<div class="kpis">'
                + card("صافي القيمة (NAV)", p, True)
                + card("العائد الكلّي", p, True)
                + card("مقابل Golden Core", p, True)
                + card("أقصى تراجع", p, True) + '</div>')
    tr = k["total_return"]; vg = k["vs_golden"]
    return ('<div class="kpis">'
        + card("صافي القيمة (NAV)", f'${_num(k["nav"])}')
        + card("العائد الكلّي", f'<span class="{"up" if tr>=0 else "down"}">{_pct(tr)}</span>')
        + card("مقابل Golden Core",
               f'<span class="{"up" if (vg or 0)>=0 else "down"}">{_pct(vg)}</span>'
               if vg is not None else "—")
        + card("أقصى تراجع", f'<span class="down">{_pct(k["max_drawdown"])}</span>')
        + '</div>')


def _render_heatmap(sectors: list, funnel: dict | None = None) -> str:
    # شريط قُمع الكون
    funnel_html = ""
    if funnel:
        rs = funnel.get("reasons", {})
        chips = (f'<span class="fchip">مسح <b>{funnel["scanned"]}</b></span>'
                 f'<span class="fchip live">الكون <b>{funnel["passed"]}</b></span>'
                 f'<span class="fchip">ميتة {rs.get("dead_floor",0)}</span>'
                 f'<span class="fchip">mcap {rs.get("low_mcap",0)}</span>'
                 f'<span class="fchip">سيولة {rs.get("low_liquidity",0)}</span>'
                 f'<span class="fchip">مُستبعَد {rs.get("excluded",0)}</span>')
        funnel_html = f'<div class="funnel">{chips}</div>'

    if not sectors:
        body = ('<div class="pending-box"><div class="ph">المرحلة 2 — Sector Observatory</div>'
                'تُضاء الخريطة تلقائياً عند بناء محرّك القطاعات وتعبئة sector_metrics.</div>')
    else:
        tiles = []
        for s in sectors:
            rs = s.get("rs_90d") or s.get("momentum") or 0
            sc = s.get("sector_score") or 0
            dl = s.get("rank_delta")
            rot = ('<span class="up">▲</span>' if (dl or 0) > 0 else
                   '<span class="down">▼</span>' if (dl or 0) < 0 else
                   '<span style="color:var(--tx-faint)">•</span>')
            tiles.append(
                f'<div class="tile" style="{_heat_style(sc, rs)}">'
                f'<div class="name">{html.escape(str(s.get("sector","")))} {rot}</div>'
                f'<div><div class="rs mono {"up" if rs>=0 else "down"}">{_pct(rs)}</div>'
                f'<div class="sc">score {sc:.0f}</div></div></div>')
        body = '<div class="heat">' + "".join(tiles) + '</div>'
    return (f'<div class="card panel"><h3>خريطة القطاعات الحرارية</h3>'
            f'<div class="sub">القوة النسبية (Sector RS) — أخضر = تدفّق · ▲▼ دوران مقابل الأسبوع الماضي</div>'
            f'{funnel_html}{body}</div>')


def _render_top(top: list) -> str:
    if not top:
        body = ('<div class="pending-box"><div class="ph">المرحلة 3 — Coin Ranking</div>'
                'يظهر هنا أفضل 20 مشروعاً (العوامل الخمسة + Entry/Exit Buffer) عند بناء محرّك الترتيب.</div>')
    else:
        rows = []
        for i, t in enumerate(top, 1):
            hot = " hot" if i <= 5 else ""
            rk = f'<span class="rank {"top" if i<=5 else ""}">{i}</span>'
            rs = t.get("rs_90d")
            up = (rs or 0) >= 0
            arrow = "▲" if up else "▼"
            act = (t.get("action") or "hold").lower()
            rows.append(
                f'<tr class="{hot.strip()}"><td>{rk}</td>'
                f'<td><span class="sym">{html.escape(str(t.get("symbol","")))}</span></td>'
                f'<td><span class="tag">{html.escape(str(t.get("sector","") or "—"))}</span></td>'
                f'<td class="mono {"up" if up else "down"}"><span class="arrow">{arrow}</span>{_pct(rs)}</td>'
                f'<td><span class="pill {act}">{act}</span></td></tr>')
        body = ('<table class="tbl"><thead><tr><th>#</th><th>العملة</th>'
                '<th>القطاع</th><th>RS 90d</th><th>الحالة</th></tr></thead>'
                '<tbody>' + "".join(rows) + '</tbody></table>')
    return (f'<div class="card panel" style="margin-bottom:18px">'
            f'<h3>المشاريع المختارة — Top 20</h3>'
            f'<div class="sub">أعلى 5 مميّزة — مرشّحو أقوى القطاعات وقت Risk-On</div>'
            f'{body}</div>')


def _render_chart(nav: dict | None) -> str:
    if not nav:
        inner = ('<div class="pending-box"><div class="ph">المراحل 3–5 — Paper Portfolio</div>'
                 'منحنى الأداء (الاستراتيجية مقابل Golden Core) يتراكم أسبوعياً بعد تشغيل المحفظة.</div>')
    else:
        inner = '<div class="chart-box"><canvas id="navChart"></canvas></div>'
    return (f'<div class="card panel"><h3>الأداء التراكمي</h3>'
            f'<div class="sub">صافي القيمة — الاستراتيجية (C) مقابل النواة الذهبية</div>'
            f'{inner}</div>')


def _render_footer(phases: dict) -> str:
    def tag(name, on):
        return f'<span class="ptag {"live" if on else "pend"}">{name}{" ●" if on else " ○"}</span>'
    tags = (tag("Regime", phases.get("regime")) + tag("Sectors", phases.get("sectors"))
            + tag("Ranking", phases.get("ranking")) + tag("Portfolio", phases.get("portfolio")))
    build = f'Build: {C.BUILD_VERSION} - {C.BUILD_STAGE}'
    return (f'<div class="foot panel"><div>Crypto Observatory · '
            f'بيانات: Binance · CoinGecko · DefiLlama · alternative.me '
            f'· <span class="mono" style="color:var(--on)">{build}</span></div>'
            f'<div class="phase-tags">{tags}</div></div>')


# ===========================================================================
# 5) التجميع + الكتابة
# ===========================================================================
def render_html(data: dict) -> str:
    nav_json = json.dumps(data["nav"]) if data["nav"] else "null"
    js = _JS.replace("__NAV_JSON__", nav_json)
    body = (_render_hero(data["market"]) + _render_kpis(data["kpis"])
            + '<div class="grid">' + _render_chart(data["nav"])
            + _render_heatmap(data["sectors"], data.get("funnel")) + '</div>'
            + _render_top(data["top"]) + _render_footer(data["phases"]))
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Observatory — لوحة التحكم</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">{body}</div>
<script>{js}</script>
</body>
</html>"""


def generate(db_path=None, out_path=None) -> str:
    conn = A.connect(db_path)
    A.init_schema(conn)
    data = load_dashboard_data(conn)
    conn.close()
    html_str = render_html(data)
    out = out_path or (C.ROOT_DIR / "docs" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_str, encoding="utf-8")
    print(f"[reporter] كُتب {out}  (regime={'✓' if data['phases']['regime'] else '—'}, "
          f"sectors={'✓' if data['phases']['sectors'] else '—'}, "
          f"top={'✓' if data['phases']['ranking'] else '—'}, "
          f"nav={'✓' if data['phases']['portfolio'] else '—'})")
    return str(out)


if __name__ == "__main__":
    generate()
