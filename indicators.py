"""
Crypto Observatory V1 — indicators.py
دوال تقنية نقية (بلا حالة، قابلة للاختبار). مشتركة بين المرحلتين 2 و3.

كلها تعمل على pandas Series/DataFrame من الإغلاق اليومي.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


# ===========================================================================
# اتجاه (Trend)
# ===========================================================================
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def trend_up(close: pd.Series,
             fast: int = C.EMA_FAST, slow: int = C.EMA_SLOW) -> tuple[float, float, int]:
    """يُرجع (ema_fast, ema_slow, trend_up?) — الاتجاه صاعد إن fast > slow."""
    ef = ema(close, fast).iloc[-1] if len(close) >= fast else np.nan
    es = ema(close, slow).iloc[-1] if len(close) >= slow else np.nan
    up = int(ef > es) if (pd.notna(ef) and pd.notna(es)) else 0
    return float(ef) if pd.notna(ef) else np.nan, \
           float(es) if pd.notna(es) else np.nan, up


# ===========================================================================
# عائد + قوة نسبية (Returns / Relative Strength)
# ===========================================================================
def pct_return(close: pd.Series, lookback: int) -> float:
    """عائد بسيط عبر lookback يوماً."""
    if len(close) <= lookback:
        return np.nan
    p0, p1 = close.iloc[-1 - lookback], close.iloc[-1]
    return float(p1 / p0 - 1.0) if p0 else np.nan


def relative_strength(close: pd.Series, bench: pd.Series, lookback: int) -> float:
    """RS = عائد العملة − عائد المعيار عبر نفس النافذة (excess return)."""
    r_a = pct_return(close, lookback)
    r_b = pct_return(bench, lookback)
    if np.isnan(r_a) or np.isnan(r_b):
        return np.nan
    return float(r_a - r_b)


# ===========================================================================
# تذبذب (Volatility / ATR)
# ===========================================================================
def atr(df: pd.DataFrame, period: int = C.ATR_PERIOD) -> float:
    """ATR على OHLC (يتطلّب أعمدة high/low/close)."""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    a = tr.rolling(period, min_periods=period).mean().iloc[-1]
    return float(a) if pd.notna(a) else np.nan


def atr_pct(df: pd.DataFrame, period: int = C.ATR_PERIOD) -> float:
    """ATR كنسبة من السعر (قابل للمقارنة بين العملات)."""
    a = atr(df, period)
    last = float(df["close"].iloc[-1])
    return float(a / last) if (last and not np.isnan(a)) else np.nan


def volatility_score(df: pd.DataFrame) -> float:
    """
    انكماش التذبذب (VCP): ATR الحالي مقابل متوسطه على 90 يوماً.
    < 1 = انكماش (إيجابي، تكويم)؛ > 1 = توسّع. نُرجع نسبة الانكماش (أعلى=أفضل).
    """
    a_now = atr(df, C.ATR_PERIOD)
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    a_avg = tr.rolling(C.ATR_PERIOD).mean().rolling(90).mean().iloc[-1]
    if np.isnan(a_now) or np.isnan(a_avg) or not a_avg:
        return np.nan
    contraction = a_avg / a_now          # > 1 يعني انكماش (مرغوب)
    return float(contraction)


# ===========================================================================
# هيكل (Structure: HH / HL)
# ===========================================================================
def structure_hh_hl(close: pd.Series,
                    lookback: int = C.STRUCTURE_SWING_LOOKBACK) -> tuple[float, int]:
    """
    يقيس قوة الهيكل الصاعد عبر مقارنة نصفين من نافذة swing:
    قمم/قيعان النصف الأخير أعلى = هيكل صاعد (HH & HL).
    يُرجع (score 0..1, hh_hl 0/1).
    """
    w = close.iloc[-2 * lookback:]
    if len(w) < 2 * lookback:
        return np.nan, 0
    first, second = w.iloc[:lookback], w.iloc[lookback:]
    hh = second.max() > first.max()
    hl = second.min() > first.min()
    score = (int(hh) + int(hl)) / 2.0
    return float(score), int(hh and hl)


# ===========================================================================
# حجم (Volume Expansion)
# ===========================================================================
def volume_expansion(volume: pd.Series,
                     lookback: int = C.VOL_EXPANSION_LOOKBACK) -> float:
    """حجم آخر يوم مقابل متوسط lookback (>1 = توسّع حجمي)."""
    if len(volume) <= lookback:
        return np.nan
    avg = volume.iloc[-1 - lookback:-1].mean()
    return float(volume.iloc[-1] / avg) if avg else np.nan


# ===========================================================================
# متانة: قمع الشواذّ + الترتيب الرُتَبي
# ===========================================================================
def winsorize(s: pd.Series, pct: float = C.WINSOR_PCT) -> pd.Series:
    """يقصّ الطرفين عند المئينات [pct, 1-pct] (يعالج تركّز العائد في الشواذّ)."""
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(pct), s.quantile(1 - pct)
    return s.clip(lower=lo, upper=hi)


def rank_pct(s: pd.Series, ascending: bool = True) -> pd.Series:
    """رتبة مئوية 0..100 (متينة ضد المقياس). ascending=False يجعل الأعلى=100."""
    r = s.rank(ascending=ascending, pct=True, na_option="keep")
    return r * 100.0


def robust_mean(values: pd.Series, mode: str = C.SECTOR_AGG,
                weights: pd.Series | None = None) -> float:
    """تجميع متين: median (افتراضي) أو cap_weighted."""
    v = values.dropna()
    if v.empty:
        return np.nan
    if mode == "cap_weighted" and weights is not None:
        w = weights.reindex(v.index).fillna(0)
        return float((v * w).sum() / w.sum()) if w.sum() else float(v.median())
    return float(v.median())
