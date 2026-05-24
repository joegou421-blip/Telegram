import yfinance as yf
import requests
import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime, timedelta
from config.settings import POLYGON_API_KEY

logger = logging.getLogger(__name__)


class DataFetcher:
    """多源數據抓取，自動 fallback，解決數據缺失問題"""

    def __init__(self):
        self.polygon_key = POLYGON_API_KEY
        self.cache = {}

    # ─── 公開接口 ────────────────────────────────────────────

    def get_ohlcv(self, ticker: str, days: int = 365) -> pd.DataFrame | None:
        """抓取 OHLCV 數據，失敗自動換源"""
        cache_key = f"{ticker}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        sources = [
            self._from_polygon,
            self._from_yfinance,
        ]
        for source in sources:
            try:
                df = source(ticker, days)
                if self._validate(df):
                    self.cache[cache_key] = df
                    logger.info(f"{ticker}: 數據來自 {source.__name__}")
                    return df
            except Exception as e:
                logger.warning(f"{ticker} [{source.__name__}] 失敗: {e}")
                continue

        logger.error(f"{ticker}: 所有數據源失敗")
        return None

    def get_spy_ohlcv(self, days: int = 365) -> pd.DataFrame | None:
        """抓取 SPY 數據（用於 RS Line 計算）"""
        return self.get_ohlcv("SPY", days)

    def get_info(self, ticker: str) -> dict:
        """抓取股票基本信息"""
        try:
            info = yf.Ticker(ticker).info
            return {
                "market_cap":    info.get("marketCap", 0),
                "sector":        info.get("sector", "Unknown"),
                "industry":      info.get("industry", "Unknown"),
                "beta":          info.get("beta", 1.0),
                "short_name":    info.get("shortName", ticker),
                "earnings_date": self._get_next_earnings(ticker),
            }
        except Exception as e:
            logger.warning(f"{ticker} info 失敗: {e}")
            return {}

    def get_financials(self, ticker: str) -> dict:
        """抓取基本面數據"""
        try:
            t = yf.Ticker(ticker)
            income = t.quarterly_financials
            info   = t.info

            eps_list = []
            rev_list = []

            if income is not None and not income.empty:
                if "Net Income" in income.index:
                    eps_list = income.loc["Net Income"].dropna().tolist()[:4]
                if "Total Revenue" in income.index:
                    rev_list = income.loc["Total Revenue"].dropna().tolist()[:4]

            return {
                "eps_quarters":          eps_list,
                "revenue_quarters":      rev_list,
                "gross_margin":          info.get("grossMargins", 0),
                "institutional_pct":     info.get("institutionPercent", 0),
                "institutional_change":  info.get("heldPercentInstitutions", 0),
                "forward_pe":            info.get("forwardPE", 0),
                "peg_ratio":             info.get("pegRatio", 0),
            }
        except Exception as e:
            logger.warning(f"{ticker} financials 失敗: {e}")
            return {}

    # ─── 私有方法 ─────────────────────────────────────────────

    def _from_polygon(self, ticker: str, days: int) -> pd.DataFrame:
        """Polygon.io 數據源"""
        if not self.polygon_key:
            raise ValueError("無 Polygon API key")

        end   = datetime.now()
        start = end - timedelta(days=days)
        url   = (
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
            f"?adjusted=true&sort=asc&limit=500&apiKey={self.polygon_key}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("resultsCount", 0) == 0:
            raise ValueError(f"Polygon 無數據: {ticker}")

        df = pd.DataFrame(data["results"])
        df["date"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={
            "o": "open", "h": "high", "l": "low",
            "c": "close", "v": "volume"
        })
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
        return df

    def _from_yfinance(self, ticker: str, days: int) -> pd.DataFrame:
        """yfinance 備用數據源"""
        period = f"{days}d" if days <= 729 else "2y"
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty:
            raise ValueError(f"yfinance 無數據: {ticker}")
        df.columns = [c.lower() for c in df.columns]
        return df

    def _validate(self, df) -> bool:
        """驗證數據完整性"""
        if df is None or df.empty:
            return False
        if len(df) < 60:
            return False
        null_pct = df["close"].isnull().mean()
        if null_pct > 0.05:
            return False
        # 檢查異常值（收盤價不能為 0 或負數）
        if (df["close"] <= 0).any():
            return False
        return True

    def _get_next_earnings(self, ticker: str):
        """抓取下次 Earnings 日期"""
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is not None and not cal.empty:
                dates = cal.get("Earnings Date", [])
                if len(dates) > 0:
                    return pd.Timestamp(dates[0])
        except Exception:
            pass
        return None
