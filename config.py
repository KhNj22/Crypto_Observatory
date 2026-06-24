"""
Crypto Observatory V1 — config.py
الإعدادات المركزية + الحاجز الصلب للورق + أدوات UTC + تعريف أذرع الـ A/B.

كل القرارات القابلة للضبط في مكان واحد، حتى لا تتسرّب أرقام سحرية إلى الكود.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

# ===========================================================================
# 0) الحاجز الصلب — لا تداول حقيقي في V1 إطلاقاً
# ===========================================================================
LIVE_TRADING: bool = False  # أي محاولة تداول حقيقي يجب أن تفشل بـ assert

def assert_paper_only() -> None:
    """يُستدعى في أي مسار يكتب صفقة. يضمن أن النظام ورقي بحت."""
    assert LIVE_TRADING is False, (
        "LIVE_TRADING=True غير مسموح في V1 — النظام ورقي بحت. "
        "لا يوجد ربط بأي endpoint تداول حقيقي."
    )

# ===========================================================================
# 1) المسارات
# ===========================================================================
ROOT_DIR     = Path(__file__).resolve().parent
DATA_DIR     = ROOT_DIR / "data"
DB_PATH      = DATA_DIR / "observatory.db"      # أرشيف SQLite (المُشتقّات + اللقطات)
OHLCV_DIR    = DATA_DIR / "ohlcv"               # OHLCV خام (Parquet) — خارج القاعدة
EXPORTS_DIR  = DATA_DIR / "exports"             # لقطات CSV أسبوعية (لـ git versioning)
SCHEMA_PATH  = ROOT_DIR / "schema.sql"

for _d in (DATA_DIR, OHLCV_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ===========================================================================
# 2) إصدار المخطط (للهجرات المستقبلية — PRAGMA user_version)
# ===========================================================================
SCHEMA_VERSION: int = 1
BUILD_VERSION: str = "V1.0"        # يظهر في تذييل الداش بورد
BUILD_STAGE: str = "Live"          # Live | Beta | Dev

# ===========================================================================
# 3) أدوات الوقت — كل شيء UTC (متسق مع شموع Binance وعمل SMA200 السابق)
# ===========================================================================
def now_ms() -> int:
    """epoch بالميلي ثانية (UTC)."""
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)

def now_iso() -> str:
    """طابع زمني ISO-8601 UTC كامل."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

def today_utc() -> str:
    """تاريخ اليوم UTC بصيغة 'YYYY-MM-DD' (مفتاح التشغيل)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

def week_key(date_str: str | None = None) -> str:
    """مرساة الأسبوع ISO 'YYYY-Www' (للربط الأسبوعي الثابت)."""
    d = (_dt.date.fromisoformat(date_str) if date_str
         else _dt.datetime.now(_dt.timezone.utc).date())
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"

# ===========================================================================
# 4) فلاتر الكون (Universe)
# ===========================================================================
UNIVERSE_TOP_N        = 300        # نأخذ أفضل ~300 بالـ market cap كنقطة بداية
TARGET_UNIVERSE_SIZE  = (150, 250) # الحجم المستهدف بعد الفلترة (أدنى، أقصى)

# --- مفتاح وضع الكون (يحسم التضارب بين الاتّساع والصلاحية بمفتاح واحد) ---
#   "quality" (موصى به) → البوّابة $200M سوقية / $5M سيولة. RS موثوق، أسعار قابلة للتنفيذ.
#   "wide"             → البوّابة هي الأرضية $1M فقط (كما في المواصفات). كون أوسع، لكن
#                         RS أكثر ضوضاءً وتلاعباً (micro-caps). للاستكشاف لا للتداول.
UNIVERSE_MODE         = "quality"     # "quality" | "wide"  ← بدّل هنا فقط

# --- طبقتان للفلترة ---
# 1) أرضية "ميتة" مطلقة — أي شيء أدناها يُرفض فوراً في كل الأوضاع.
DEAD_FLOOR_USD        = 1_000_000     # 1M (vol أو mcap تحتها = ميتة)
# 2) عتبة دخول الكون الفعلي (طبقة الجودة الموصى بها — تطابق مستندك الأصلي).
MIN_MARKET_CAP_USD    = 200_000_000   # 200M
MIN_VOLUME_24H_USD    = 5_000_000     # 5M (سيولة حقيقية، سبريد ضيّق)
# ملاحظة: "wide" يعيد ضوضاء وتلاعب الـ micro-caps الذي قتل Diamond — استخدمه للاستكشاف فقط.


def universe_gates() -> tuple[float, float]:
    """يُرجع (عتبة mcap, عتبة volume) النشطة حسب UNIVERSE_MODE."""
    if UNIVERSE_MODE == "wide":
        return DEAD_FLOOR_USD, DEAD_FLOOR_USD
    return MIN_MARKET_CAP_USD, MIN_VOLUME_24H_USD

MIN_AGE_DAYS          = 365            # عمر > سنة
QUOTE_ASSET           = "USDT"
SPOT_ONLY             = True

# --- قائمة الاستبعاد التلقائي ---
# عملات مستقرّة (لا معنى لـ RS لها)
STABLECOINS = {
    "USDT","USDC","FDUSD","TUSD","DAI","BUSD","USDP","USDD","GUSD","PYUSD",
    "USDE","FRAX","LUSD","EURT","EURS","EUR","AEUR","USTC","SUSD","CUSD",
    "USD1","RLUSD","USDG","XUSD",
}
# عملات مُغلّفة/مكرّرة (تكرّر أصلاً موجوداً → تشوّه RS)
WRAPPED = {"WBTC","WETH","WBETH","WEETH","STETH","WSTETH","CBETH","RETH","BETH"}
# أنماط رموز الرافعة المالية (تُستبعَد بالـ regex)
LEVERAGED_PATTERNS = (
    r".+(UP|DOWN)$",          # BTCUP / BTCDOWN (Binance)
    r".+(BULL|BEAR)$",        # XXXBULL / XXXBEAR
    r".+\d+(L|S)$",           # BTC3L / ETH3S / 5L / 5S
)

# مفردات القطاعات المعتمدة (vocabulary)
SECTOR_VOCAB = ("AI","RWA","L1","L2","DeFi","Meme","DePIN","Gaming",
                "Infra","Oracle","Exchange","Privacy","Payments","Other")

# أسباب الفشل المعيارية (تُخزَّن في universe.fail_reason)
FAIL_DEAD          = "dead_floor"
FAIL_LOW_LIQUIDITY = "low_liquidity"
FAIL_TOO_YOUNG     = "too_young"
FAIL_RANK_OUT      = "rank_out"
FAIL_NO_BINANCE    = "no_binance"
FAIL_LOW_MCAP      = "low_mcap"
FAIL_EXCLUDED      = "excluded"        # stable/wrapped/leveraged

# ===========================================================================
# 5) العوامل الخمسة + الترتيب
# ===========================================================================
# الأوزان مبدئياً متساوية؛ تُعامَل كذراع A/B لاحقاً لا كقرار مسبق.
RANK_FACTORS = ("trend", "rs", "volume", "structure", "volatility")
FACTOR_WEIGHTS_EQUAL = {f: 1.0 / len(RANK_FACTORS) for f in RANK_FACTORS}

EMA_FAST = 50
EMA_SLOW = 200
RS_LOOKBACK_DAYS = 90
RS_LOOKBACK_SHORT = 30
VOL_EXPANSION_LOOKBACK = 30
ATR_PERIOD = 14
STRUCTURE_SWING_LOOKBACK = 20   # لكشف HH/HL

# --- متانة الحساب (من دروس Diamond: عالج تركّز العائد في الشواذّ) ---
WINSOR_PCT = 0.02               # قمع 2% من كل طرف قبل حساب RS
SECTOR_AGG = "median"           # median (متين) أو "cap_weighted"
MIN_SECTOR_MEMBERS = 3          # أقل من ذلك → القطاع غير موثوق إحصائياً
VOLATILITY_MODE = "contraction" # نكافئ انكماش التذبذب (VCP) لا توسّعه

# ===========================================================================
# 6) Entry / Exit Buffer (تقليل الـ Turnover)
# ===========================================================================
TOP_N        = 20    # حجم القائمة المستهدفة
ENTRY_RANK   = 15    # يدخل فقط إذا أصبح ضمن أفضل 15
EXIT_RANK    = 30    # يخرج فقط إذا هبط تحت 30

# ===========================================================================
# 7) State Machine (Market Observatory) — مع Hysteresis
# ===========================================================================
SMA_REGIME_PERIOD = 200
HYSTERESIS_PCT    = 0.02   # 2% هامش حول SMA200 لقتل الـ whipsaw
CONFIRM_DAYS      = 2      # تأكيد قبل التحوّل (يطابق منهج النواة الذهبية)
REGIME_RISK_ON    = "risk_on"
REGIME_RISK_OFF   = "risk_off"
REGIME_CAUTION    = "caution"   # اختياري — يُختبر كذراع

# ===========================================================================
# 8) الرسوم و Slippage (واقعية — لا تتجاهلها وإلا بالغت النتائج)
# ===========================================================================
TAKER_FEE      = 0.001     # 0.1% رسوم Binance Spot التقريبية
SLIPPAGE_BPS   = 15        # 15 نقطة أساس انزلاق افتراضي للـ alts (سبريد واسع)
INITIAL_CAPITAL = 10_000.0

# ===========================================================================
# 9) أذرع الـ A/B + المعايير (تُزرع في جدول arms)
#    (code, name, weighting, uses_sector, uses_market_filter, is_benchmark)
# ===========================================================================
ARM_DEFINITIONS = [
    # — أذرع الاستراتيجية —
    ("A",     "RS only",                  "equal",       0, 0, 0),
    ("B",     "RS + Sector",              "equal",       1, 0, 0),
    ("C",     "RS + Sector + Market",     "equal",       1, 1, 0),
    ("C_rp",  "Full (Risk-Parity)",       "risk_parity", 1, 1, 0),
    ("C_tier","Full (Tiered 40/40/20)",   "tiered",      1, 1, 0),
    # — المعايير (benchmarks) —
    ("D",     "Golden Core (SMA200)",     "fixed",       0, 1, 1),  # BTC/ETH مُبدَّل
    ("E",     "BTC/ETH 50-50 hold",       "fixed",       0, 0, 1),
    ("F",     "Equal-Weight Universe",    "equal",       0, 0, 1),
    ("G",     "BTC hold",                 "fixed",       0, 0, 1),
    ("H",     "ETH hold",                 "fixed",       0, 0, 1),
]
# ملاحظة: المعيار الحقيقي الذي يجب هزيمته = D (Golden Core)، لا مجرد الاحتفاظ السلبي.

# ===========================================================================
# 10) مؤشرات الـ KPI (تُحسب في مرحلة التحليل من سلسلة NAV)
# ===========================================================================
KPI_METRICS = ("sortino", "max_drawdown", "ulcer_index", "cagr", "volatility", "sharpe")
PRIMARY_KPI = "sortino"          # الحكم معدّل بالمخاطرة، لا العائد الخام
RISK_FREE_RATE = 0.0
