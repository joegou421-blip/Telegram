import pandas as pd
import numpy as np


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calc_rs_line(stock_close: pd.Series, spy_close: pd.Series) -> pd.Series:
    aligned = pd.DataFrame({"stock": stock_close, "spy": spy_close}).dropna()
    return aligned["stock"] / aligned["spy"]


def calc_rs_rating(stock_close: pd.Series, spy_close: pd.Series) -> float:
    """
    RS Rating：過去12個月表現 vs SPY
    最近3個月權重 25%，前9個月權重 75%
    回傳 0-100 的評分（近似 IBD RS Rating）
    """
    if len(stock_close) < 252 or len(spy_close) < 252:
        return 50.0

    # 對齊數據
    df = pd.DataFrame({"stock": stock_close, "spy": spy_close}).dropna()
    if len(df) < 252:
        return 50.0

    s = df["stock"]
    spy = df["spy"]

    # 計算各段漲幅
    def pct(series, start, end):
        try:
            return (series.iloc[-end] / series.iloc[-start] - 1)
        except Exception:
            return 0.0

    # 最近3個月（63個交易日）vs 前9個月
    recent_stock = pct(s, 1, 63)
    old_stock    = pct(s, 63, 252)
    recent_spy   = pct(spy, 1, 63)
    old_spy      = pct(spy, 63, 252)

    # 加權相對表現
    stock_score = recent_stock * 0.25 + old_stock * 0.75
    spy_score   = recent_spy   * 0.25 + old_spy   * 0.75
    relative    = stock_score - spy_score

    # 轉成 0-100（以 ±30% 相對表現為邊界）
    rating = 50 + (relative / 0.60) * 50
    return round(max(0, min(100, rating)), 1)


def calc_volume_dryness(df: pd.DataFrame, lookback: int = 5, baseline: int = 20) -> float:
    """
    突破前成交量乾燥度
    最近 N 天成交量 vs 20 天均量的比值
    比值越低越好，< 0.6 是理想狀態
    """
    if len(df) < baseline:
        return 1.0
    recent_avg  = df["volume"].tail(lookback).mean()
    baseline_avg = df["volume"].tail(baseline).mean()
    if baseline_avg == 0:
        return 1.0
    return round(recent_avg / baseline_avg, 2)


def calc_volume_ratio(df: pd.DataFrame, short: int = 20, long: int = 60) -> float:
    if len(df) < long:
        return 1.0
    short_avg = df["volume"].tail(short).mean()
    long_avg  = df["volume"].tail(long).mean()
    return round(short_avg / long_avg, 2) if long_avg > 0 else 1.0


def calc_updown_volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    recent = df.tail(period).copy()
    recent["chg"] = recent["close"].diff()
    up_vol   = recent.loc[recent["chg"] > 0, "volume"].mean()
    down_vol = recent.loc[recent["chg"] < 0, "volume"].mean()
    if down_vol == 0 or np.isnan(down_vol):
        return 2.0
    return round(up_vol / down_vol, 2)


def calc_weekly_ema_alignment(df: pd.DataFrame) -> dict:
    """
    把日線數據重採樣成週線，判斷週線 EMA 排列
    """
    try:
        weekly = df["close"].resample("W").last().dropna()
        if len(weekly) < 40:
            return {"aligned": False, "ema10w": None, "ema20w": None, "ema40w": None}

        ema10w = weekly.ewm(span=10, adjust=False).mean()
        ema20w = weekly.ewm(span=20, adjust=False).mean()
        ema40w = weekly.ewm(span=40, adjust=False).mean()

        price  = weekly.iloc[-1]
        e10    = ema10w.iloc[-1]
        e20    = ema20w.iloc[-1]
        e40    = ema40w.iloc[-1]

        aligned = price > e10 > e20 > e40

        return {
            "aligned": aligned,
            "ema10w":  round(e10, 2),
            "ema20w":  round(e20, 2),
            "ema40w":  round(e40, 2),
            "price":   round(price, 2),
        }
    except Exception:
        return {"aligned": False, "ema10w": None, "ema20w": None, "ema40w": None}


def is_sma200_rising(df: pd.DataFrame, lookback: int = 20) -> bool:
    sma200 = calc_sma(df["close"], 200)
    if len(sma200.dropna()) < lookback:
        return False
    return sma200.iloc[-1] > sma200.iloc[-lookback]


def add_all_indicators(df: pd.DataFrame, spy_df: pd.DataFrame = None) -> pd.DataFrame:
    df = df.copy()
    df["ema20"]  = calc_ema(df["close"], 20)
    df["ema50"]  = calc_ema(df["close"], 50)
    df["ema150"] = calc_ema(df["close"], 150)
    df["ema200"] = calc_ema(df["close"], 200)
    df["sma200"] = calc_sma(df["close"], 200)
    df["atr14"]  = calc_atr(df, 14)
    df["vol_sma20"] = calc_sma(df["volume"], 20)
    df["vol_sma50"] = calc_sma(df["volume"], 50)
    df["52w_high"]  = df["high"].rolling(252).max()
    df["52w_low"]   = df["low"].rolling(252).min()
    if spy_df is not None:
        df["rs_line"] = calc_rs_line(df["close"], spy_df["close"])
    return df
