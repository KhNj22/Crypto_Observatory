"""اختبارات محرّك المحفظة (المرحلة 5) — نقيّة، offline."""
import config as C
import portfolio as P

ARMS = {a[0]: a for a in C.ARM_DEFINITIONS}
BTC, ETH = "bitcoin", "ethereum"

SEL = [  # اختيارات Top مع القطاع والترتيب والـ tier والتقلّب
    {"coin_id": "tao",  "symbol": "TAO",  "sector": "AI",  "composite_rank": 1, "in_top": 1, "tier": 1, "atr_pct": 0.06},
    {"coin_id": "ondo", "symbol": "ONDO", "sector": "RWA", "composite_rank": 2, "in_top": 1, "tier": 1, "atr_pct": 0.04},
    {"coin_id": "sol",  "symbol": "SOL",  "sector": "L1",  "composite_rank": 3, "in_top": 1, "tier": 2, "atr_pct": 0.05},
    {"coin_id": "uni",  "symbol": "UNI",  "sector": "DeFi","composite_rank": 4, "in_top": 1, "tier": 3, "atr_pct": 0.08},
]
SECTORS = [  # AI/RWA/L1 قوية، DeFi سالبة
    {"sector": "AI", "rs_90d": 0.25, "sector_rank": 1},
    {"sector": "RWA", "rs_90d": 0.18, "sector_rank": 2},
    {"sector": "L1", "rs_90d": 0.05, "sector_rank": 3},
    {"sector": "DeFi", "rs_90d": -0.04, "sector_rank": 4},
]
PRICE = {"tao": 500.0, "ondo": 1.2, "sol": 150.0, "uni": 10.0, BTC: 64000.0, ETH: 3200.0}
UNI_IDS = ["tao", "ondo", "sol", "uni"]


def w(arm_code, regime):
    return P.target_weights(ARMS[arm_code], SEL, SECTORS, regime, BTC, ETH, UNI_IDS)


def approx(a, b, t=1e-6):
    return abs(a - b) <= t


print("\n" + "=" * 66)
print("  1) بوّابة الحالة — أذرع use_market تخرج نقداً عند risk_off")
print("=" * 66)
assert w("C", C.REGIME_RISK_OFF) == {}, "C يجب أن يخرج نقداً"
assert w("D", C.REGIME_RISK_OFF) == {}, "Golden Core يجب أن يخرج نقداً"
assert w("A", C.REGIME_RISK_OFF) != {}, "A (بلا بوّابة سوق) يبقى مستثمراً"
assert w("E", C.REGIME_RISK_OFF) == {BTC: 0.5, ETH: 0.5}, "E معيار سلبي يبقى"
print("  C→نقد ✓ | D→نقد ✓ | A→يبقى ✓ | E→يبقى ✓")

print("\n" + "=" * 66)
print("  2) Golden Core + المعايير الثابتة")
print("=" * 66)
assert w("D", C.REGIME_RISK_ON) == {BTC: 0.5, ETH: 0.5}
assert w("G", C.REGIME_RISK_ON) == {BTC: 1.0}
assert w("H", C.REGIME_RISK_ON) == {ETH: 1.0}
assert set(w("F", C.REGIME_RISK_ON)) == set(UNI_IDS)
print("  D=BTC/ETH ✓ | G=BTC ✓ | H=ETH ✓ | F=كل الكون ✓")

print("\n" + "=" * 66)
print("  3) فلتر القطاع (B/C) يستبعد DeFi السالب")
print("=" * 66)
wc = w("C", C.REGIME_RISK_ON)
assert "uni" not in wc, "UNI (DeFi سالب) يجب أن يُستبعد من C"
assert "tao" in wc and "ondo" in wc, "AI/RWA القوية تبقى"
wa = w("A", C.REGIME_RISK_ON)
assert "uni" in wa, "A بلا فلتر قطاع → يشمل UNI"
print(f"  C يحمل {sorted(wc)} (بلا uni) ✓ | A يحمل uni ✓")

print("\n" + "=" * 66)
print("  4) الترجيح: risk_parity (عكس التقلّب) و tiered (40/40/20)")
print("=" * 66)
wrp = P.target_weights(ARMS["C_rp"], SEL, SECTORS, C.REGIME_RISK_ON, BTC, ETH, UNI_IDS)
# ondo أقلّ تقلّباً (0.04) من tao (0.06) → وزن أكبر
assert wrp["ondo"] > wrp["tao"], "risk_parity: الأقلّ تقلّباً وزنه أكبر"
assert approx(sum(wrp.values()), 1.0, 1e-6)
wt = P.target_weights(ARMS["C_tier"], SEL, SECTORS, C.REGIME_RISK_ON, BTC, ETH, UNI_IDS)
# tao+ondo (tier1) معاً 40%, sol (tier2) 40% — لكن uni مُستبعد بفلتر القطاع، فيبقى tier1/tier2
assert approx(sum(wt.values()), 1.0, 1e-6)
print(f"  risk_parity ondo={wrp['ondo']:.3f} > tao={wrp['tao']:.3f} ✓ | tiered Σ=1 ✓")

print("\n" + "=" * 66)
print("  5) إعادة التوازن — التكلفة تخصم من NAV، و NAV=cash+invested")
print("=" * 66)
# تشغيل أول: نقد كامل → شراء أوزان C
st = P.rebalance({}, C.INITIAL_CAPITAL, wc, PRICE)
cost_expected = sum(t["gross_value"] for t in st["trades"]) * (C.TAKER_FEE + C.SLIPPAGE_BPS/10000)
assert st["nav"] < C.INITIAL_CAPITAL, "NAV يجب أن ينقص بمقدار التكلفة"
assert approx(C.INITIAL_CAPITAL - st["nav"], st["fees_paid"], 1e-3), "نقص NAV = الرسوم"
assert approx(st["nav"], st["cash"] + st["invested_value"], 1e-2), "NAV=cash+invested"
assert all(t["side"] == "buy" for t in st["trades"]), "أول تشغيل = شراء فقط"
print(f"  NAV0={C.INITIAL_CAPITAL} → NAV1={st['nav']:.2f} (تكلفة={st['fees_paid']:.2f}, "
      f"{st['n_positions']} مراكز) ✓")

print("\n" + "=" * 66)
print("  6) الخروج النقدي من مراكز قائمة يكلّف (بيع)")
print("=" * 66)
st2 = P.rebalance(st["units"], st["cash"], {}, PRICE)   # weights فارغة = خروج
assert st2["n_positions"] == 0 and st2["invested_value"] == 0, "خروج كامل للنقد"
assert all(t["side"] == "sell" for t in st2["trades"]), "كلها بيع"
assert st2["nav"] < st["nav"], "الخروج يكلّف"
assert approx(st2["cash"], st2["nav"], 1e-2)
print(f"  خروج: NAV {st['nav']:.2f} → {st2['nav']:.2f} (نقد كامل) ✓")

print("\n" + "=" * 66)
print("  النتيجة")
print("=" * 66)
print("جميع اختبارات محرّك المحفظة نجحت ✓")
print("بوّابة الحالة + Golden Core + فلتر القطاع + الترجيح + التكلفة — كلها صحيحة.")
