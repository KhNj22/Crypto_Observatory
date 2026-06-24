-- ===========================================================================
-- Crypto Observatory V1  —  Archive Schema (المرحلة 0)
-- الأرشيف نقطة-زمنية (Point-In-Time) — البنية الوحيدة غير القابلة للإصلاح بأثر رجعي.
-- كل المحرّكات تكتب هنا. لا يُكتب فوق التاريخ أبداً (append-only / immutable).
-- محرّك: SQLite (ACID = كتابة ذرّية). الـ OHLCV الخام يبقى في Parquet خارج هذه القاعدة.
-- ===========================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- meta : إصدار المخطط + بيانات وصفية
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- coins : بُعد الهوية الثابتة. المفتاح = CoinGecko id (مثل 'bitcoin').
-- لا نخزّن القطاع هنا (القطاع نقطة-زمنية → يُخزَّن في universe/coin_metrics).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coins (
    coin_id         TEXT PRIMARY KEY,          -- CoinGecko id (الهوية المستقرّة)
    name            TEXT,
    symbol          TEXT,                       -- الرمز الحالي (قد يتغيّر — للعرض فقط)
    genesis_date    TEXT,                       -- ISO date (nullable)
    first_seen_date TEXT,                       -- أول تاريخ رأيناه فيه العملة
    created_at      INTEGER NOT NULL            -- epoch_ms
);

-- ---------------------------------------------------------------------------
-- coin_exchange_map : خريطة العملة → زوج التداول في بورصة (يتيح ccxt لاحقاً)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coin_exchange_map (
    coin_id       TEXT NOT NULL,
    exchange      TEXT NOT NULL DEFAULT 'binance',
    pair_symbol   TEXT NOT NULL,               -- مثل 'BTCUSDT'
    quote_asset   TEXT NOT NULL DEFAULT 'USDT',
    active        INTEGER NOT NULL DEFAULT 1,   -- 0/1
    PRIMARY KEY (coin_id, exchange, pair_symbol),
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- ---------------------------------------------------------------------------
-- arms : أذرع الاستراتيجية + المعايير (A/B). المعايير أذرع بعلم is_benchmark.
--   weighting_scheme: 'equal' | 'risk_parity' | 'tiered' | 'fixed'
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS arms (
    arm_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT NOT NULL UNIQUE,     -- 'A','B','C','C_rp','D'...
    name              TEXT NOT NULL,
    description       TEXT,
    weighting_scheme  TEXT NOT NULL DEFAULT 'equal',
    uses_sector       INTEGER NOT NULL DEFAULT 0,
    uses_market_filter INTEGER NOT NULL DEFAULT 0,
    is_benchmark      INTEGER NOT NULL DEFAULT 0,
    initial_capital   REAL NOT NULL DEFAULT 10000.0,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- runs : كل تشغيل (يومي/أسبوعي/تحوّل). مفتاح Idempotency = (run_date, run_type).
--   status: 'running' | 'ok' | 'failed'
--   completed_at IS NULL  ⇒  تشغيل لم يكتمل (يُنظَّف عند إعادة المحاولة)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,              -- ISO date UTC 'YYYY-MM-DD'
    run_type        TEXT NOT NULL,              -- 'daily' | 'weekly' | 'flip'
    started_at      INTEGER NOT NULL,           -- epoch_ms
    completed_at    INTEGER,                    -- epoch_ms (NULL = فشل/جزئي)
    status          TEXT NOT NULL DEFAULT 'running',
    data_complete   INTEGER NOT NULL DEFAULT 0, -- 0/1 تحقّق الاكتمال قبل COMMIT
    content_hash    TEXT,                       -- sha256 للتكرارية
    schema_version  INTEGER NOT NULL,
    notes           TEXT,
    UNIQUE (run_date, run_type)
);

-- ---------------------------------------------------------------------------
-- market_state : لقطة Market Observatory (تُكتب كل تشغيل، يومي وأسبوعي).
--   regime: 'risk_on' | 'risk_off' | 'caution'
--   البنود nullable = تأكيدية (BTC.D/TOTAL) قد لا تتوفّر تاريخياً مجاناً.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_state (
    run_id              INTEGER PRIMARY KEY,
    ts_utc              TEXT NOT NULL,
    regime              TEXT NOT NULL,
    regime_raw          TEXT,                    -- الحالة قبل الـ hysteresis
    btc_close           REAL,
    btc_sma200          REAL,
    btc_dist_pct        REAL,                    -- (close - sma)/sma
    hysteresis_pct      REAL,
    confirm_days        INTEGER,
    stablecoin_mcap     REAL,
    stablecoin_trend_30d REAL,                   -- نسبة تغيّر 30 يوم
    fear_greed          INTEGER,
    fear_greed_label    TEXT,
    breadth_pct_above_sma REAL,
    advance_decline     REAL,
    btc_dominance       REAL,                    -- nullable (تأكيدي)
    total_mcap          REAL,                    -- nullable
    total2_mcap         REAL,                    -- nullable
    total3_mcap         REAL,                    -- nullable
    eth_btc             REAL,                    -- nullable
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- universe : عضوية الكون نقطة-زمنية (أسبوعي). IMMUTABLE — لا يُكتب فوقه.
-- يخزّن القائمة المرتّبة الكاملة + سبب فشل كل فلتر ⇒ أمانة ضد survivorship.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS universe (
    run_id        INTEGER NOT NULL,
    run_date      TEXT NOT NULL,
    coin_id       TEXT NOT NULL,
    symbol        TEXT,
    sector        TEXT,                          -- نقطة-زمنية
    rank_by_mcap  INTEGER,
    market_cap    REAL,
    volume_24h    REAL,
    age_days      INTEGER,
    in_universe   INTEGER NOT NULL DEFAULT 0,    -- 0/1 اجتاز كل الفلاتر؟
    fail_reason   TEXT,                          -- 'low_liquidity'|'too_young'|'rank_out'|'no_binance'|NULL
    PRIMARY KEY (run_id, coin_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- ---------------------------------------------------------------------------
-- coin_metrics : العوامل الخمسة + الترتيب المركّب (أسبوعي).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coin_metrics (
    run_id          INTEGER NOT NULL,
    coin_id         TEXT NOT NULL,
    sector          TEXT,
    rank_by_mcap    INTEGER,
    -- 1) Trend
    ema50           REAL,
    ema200          REAL,
    trend_up        INTEGER,                     -- 0/1  (ema50 > ema200)
    -- 2) Relative Strength
    rs_90d          REAL,
    rs_vs_btc       REAL,
    rs_vs_total3    REAL,
    rs_vs_sector    REAL,
    -- 3) Volume
    vol_expansion   REAL,                        -- حجم آني / متوسط
    -- 4) Structure
    structure_score REAL,                        -- HH/HL مكمّماً
    structure_hh_hl INTEGER,                     -- 0/1
    -- 5) Volatility
    atr             REAL,
    atr_pct         REAL,
    -- النتيجة
    factor_rank_trend     INTEGER,
    factor_rank_rs        INTEGER,
    factor_rank_volume    INTEGER,
    factor_rank_structure INTEGER,
    factor_rank_vola      INTEGER,
    composite_rank  INTEGER,                     -- الترتيب النهائي المستخدم للاختيار
    PRIMARY KEY (run_id, coin_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- ---------------------------------------------------------------------------
-- sector_metrics : لقطة Sector Observatory (أسبوعي).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sector_metrics (
    run_id        INTEGER NOT NULL,
    sector        TEXT NOT NULL,
    rs_30d        REAL,
    rs_90d        REAL,
    momentum      REAL,
    breadth       REAL,                          -- % عملات القطاع فوق MA
    new_high_ratio REAL,
    mcap_share    REAL,
    sector_score  REAL,
    sector_rank   INTEGER,
    PRIMARY KEY (run_id, sector),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- selections : Top20 مع Entry/Exit Buffer (أسبوعي).
--   action: 'enter' | 'hold' | 'exit'   (مقابل التشغيل الأسبوعي السابق)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS selections (
    run_id         INTEGER NOT NULL,
    coin_id        TEXT NOT NULL,
    symbol         TEXT,
    sector         TEXT,
    composite_rank INTEGER,
    action         TEXT NOT NULL,                -- enter|hold|exit
    in_top         INTEGER NOT NULL DEFAULT 0,   -- 0/1 ضمن الـ Top المستهدف الآن؟
    tier           INTEGER,                       -- للوزن الطبقي (1/2/3) إن استُخدم
    PRIMARY KEY (run_id, coin_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- ---------------------------------------------------------------------------
-- portfolio_state : حالة كل ذراع في كل تشغيل. سلسلة الـ NAV تُبنى من هنا.
-- (المعايير أذرع أيضاً ⇒ تُقارَن بعدالة بنفس الطريقة.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_state (
    run_id          INTEGER NOT NULL,
    arm_id          INTEGER NOT NULL,
    ts_utc          TEXT NOT NULL,
    cash            REAL NOT NULL,
    invested_value  REAL NOT NULL,
    nav             REAL NOT NULL,               -- cash + invested_value
    n_positions     INTEGER NOT NULL DEFAULT 0,
    regime_at_run   TEXT,
    turnover        REAL NOT NULL DEFAULT 0,
    fees_paid       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, arm_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (arm_id) REFERENCES arms(arm_id)
);

-- ---------------------------------------------------------------------------
-- holdings : مراكز كل ذراع في كل تشغيل (per coin).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS holdings (
    run_id        INTEGER NOT NULL,
    arm_id        INTEGER NOT NULL,
    coin_id       TEXT NOT NULL,
    symbol        TEXT,
    target_weight REAL,
    actual_weight REAL,
    units         REAL,
    entry_price   REAL,
    mark_price    REAL,
    value         REAL,
    PRIMARY KEY (run_id, arm_id, coin_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (arm_id) REFERENCES arms(arm_id),
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- ---------------------------------------------------------------------------
-- trades : الصفقات المُحاكاة (Paper). برسوم + slippage. لا تداول حقيقي.
--   side: 'buy' | 'sell'    reason: 'rebalance'|'enter'|'exit'|'derisk'
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    trade_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    arm_id       INTEGER NOT NULL,
    coin_id      TEXT NOT NULL,
    symbol       TEXT,
    side         TEXT NOT NULL,
    units        REAL NOT NULL,
    price        REAL NOT NULL,
    gross_value  REAL NOT NULL,
    fee          REAL NOT NULL DEFAULT 0,
    slippage     REAL NOT NULL DEFAULT 0,
    net_value    REAL NOT NULL,
    reason       TEXT,
    is_paper     INTEGER NOT NULL DEFAULT 1,     -- دائماً 1 في V1
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (arm_id) REFERENCES arms(arm_id),
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- ---------------------------------------------------------------------------
-- فهارس للاستعلامات الشائعة (سلاسل NAV، استعلام بالتاريخ/العملة)
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_runs_date        ON runs(run_date);
CREATE INDEX IF NOT EXISTS idx_runs_type_date   ON runs(run_type, run_date);
CREATE INDEX IF NOT EXISTS idx_universe_date    ON universe(run_date, in_universe);
CREATE INDEX IF NOT EXISTS idx_universe_coin    ON universe(coin_id);
CREATE INDEX IF NOT EXISTS idx_metrics_coin     ON coin_metrics(coin_id);
CREATE INDEX IF NOT EXISTS idx_pstate_arm       ON portfolio_state(arm_id, run_id);
CREATE INDEX IF NOT EXISTS idx_holdings_arm     ON holdings(arm_id, run_id);
CREATE INDEX IF NOT EXISTS idx_trades_arm       ON trades(arm_id, run_id);
