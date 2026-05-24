import pandas as pd
import numpy as np


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calc_rs_line(stock_close: pd.Series, spy_close: pd.Series) -> pd.Series:
    """RS Line = 股票收盤 / SPY 收盤，對齊日期"""
    aligned = pd.DataFrame({
        "stock": stock_close,
        "spy":   spy_close,
    }).dropna()
    return aligned["stock"] / aligned["spy"]


def calc_volume_ratio(df: pd.DataFrame, short: int = 20, long: int = 60) -> float:
    """近期均量 / 長期均量，> 1 代表關注度上升"""
    if len(df) < long:
        return 1.0
    short_avg = df["volume"].tail(short).mean()
    long_avg  = df["volume"].tail(long).mean()
    return round(short_avg / long_avg, 2) if long_avg > 0 else 1.0


def calc_updown_volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    """上漲日均量 / 下跌日均量，> 1.5 代表買盤強"""
    recent = df.tail(period).copy()
    recent["chg"] = recent["close"].diff()
    up_vol   = recent.loc[recent["chg"] > 0, "volume"].mean()
    down_vol = recent.loc[recent["chg"] < 0, "volume"].mean()
    if down_vol == 0 or np.isnan(down_vol):
        return 2.0
    return round(up_vol / down_vol, 2)


def get_52w_high(df: pd.DataFrame) -> float:
    return df["high"].tail(252).max()


def get_52w_low(df: pd.DataFrame) -> float:
    return df["low"].tail(252).min()


def is_sma200_rising(df: pd.DataFrame, lookback: int = 20) -> bool:
    """SMA200 是否向上傾斜"""
    sma200 = calc_sma(df["close"], 200)
    if len(sma200.dropna()) < lookback:
        return False
    return sma200.iloc[-1] > sma200.iloc[-lookback]


def add_all_indicators(df: pd.DataFrame, spy_df: pd.DataFrame = None) -> pd.DataFrame:
    """一次性加入所有技術指標"""
    df = df.copy()
    df["ema20"]  = calc_ema(df["close"], 20)
    df["ema50"]  = calc_ema(df["close"], 50)
    df["ema150"] = calc_ema(df["close"], 150)
    df["ema200"] = calc_ema(df["close"], 200)
    df["sma200"] = calc_sma(df["close"], 200)
    df["atr14"]  = calc_atr(df, 14)
    df["vol_sma20"]  = calc_sma(df["volume"], 20)
    df["vol_sma50"]  = calc_sma(df["volume"], 50)
    df["52w_high"] = df["high"].rolling(252).max()
    df["52w_low"]  = df["low"].rolling(252).min()

    if spy_df is not None:
        df["rs_line"] = calc_rs_line(df["close"], spy_df["close"])

    return df
