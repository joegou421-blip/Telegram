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

    def __init__(self):
        self.polygon_key = POLYGON_API_KEY
        self.cache = {}

    # ─── 公開接口 ────────────────────────────────────────────

    def get_ohlcv(self, ticker: str, days: int = 365) -> pd.DataFrame | None:
        cache_key = f"{ticker}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        # 本地掃描：直接用 yfinance，不用 Polygon（本地 IP 沒被封）
        # GitHub Actions 上才需要 Polygon
        sources = [self._from_yfinance, self._from_polygon]

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
        return self.get_ohlcv("SPY", days)

    def get_info(self, ticker: str) -> dict:
        """抓取股票基本信息"""
        for attempt in range(3):
            try:
                t    = yf.Ticker(ticker)
                info = t.info
                time.sleep(0.3)
                return {
                    "market_cap":    info.get("marketCap", 0),
                    "sector":        info.get("sector", "Unknown"),
                    "industry":      info.get("industry", "Unknown"),
                    "beta":          info.get("beta", 1.0),
                    "short_name":    info.get("shortName", ticker),
                    "earnings_date": self.get_next_earnings(ticker),
                }
            except Exception as e:
                err = str(e)
                if "429" in err:
                    wait = (attempt + 1) * 15
                    logger.warning(f"{ticker} info 429，等待 {wait}s...")
                    time.sleep(wait)
                else:
                    logger.warning(f"{ticker} info 失敗: {e}")
                    break
        return {}

    def get_financials(self, ticker: str) -> dict:
        """抓取基本面數據"""
        for attempt in range(3):
            try:
                t      = yf.Ticker(ticker)
                income = t.quarterly_financials
                info   = t.info
                time.sleep(0.3)

                eps_list = []
                rev_list = []
                if income is not None and not income.empty:
                    if "Net Income" in income.index:
                        eps_list = income.loc["Net Income"].dropna().tolist()[:5]
                    if "Total Revenue" in income.index:
                        rev_list = income.loc["Total Revenue"].dropna().tolist()[:5]

                return {
                    "eps_quarters":         eps_list,
                    "revenue_quarters":     rev_list,
                    "gross_margin":         info.get("grossMargins", 0),
                    "institutional_pct":    info.get("institutionPercent", 0),
                    "institutional_change": info.get("heldPercentInstitutions", 0),
                }
            except Exception as e:
                err = str(e)
                if "429" in err:
                    wait = (attempt + 1) * 15
                    logger.warning(f"{ticker} financials 429，等待 {wait}s...")
                    time.sleep(wait)
                else:
                    logger.warning(f"{ticker} financials 失敗: {e}")
                    break
        return {}

    # ─── 私有方法 ─────────────────────────────────────────────

    def _from_yfinance(self, ticker: str, days: int) -> pd.DataFrame:
        """yfinance 抓取 OHLCV，修復新版 MultiIndex columns bug"""
        period = f"{days}d" if days <= 729 else "2y"
        raw = yf.download(
            ticker,
            period=period,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw.empty:
            raise ValueError(f"yfinance 無數據: {ticker}")

        # 修復新版 yfinance MultiIndex columns 問題
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        # 統一小寫
        raw.columns = [str(c).lower() for c in raw.columns]

        if "close" not in raw.columns:
            raise ValueError(f"yfinance 數據格式錯誤: {ticker}")

        return raw

    def _from_polygon(self, ticker: str, days: int) -> pd.DataFrame:
        """Polygon 備用數據源"""
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
        if resp.status_code == 429:
            time.sleep(15)
            raise ValueError(f"Polygon 429: {ticker}")
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

    def _validate(self, df) -> bool:
        if df is None or df.empty:
            return False
        if len(df) < 60:
            return False
        if "close" not in df.columns:
            return False
        if df["close"].isnull().mean() > 0.05:
            return False
        if (df["close"] <= 0).any():
            return False
        return True

    def get_premarket_quote(self, ticker: str) -> dict | None:
        """
        抓取最新（pre-market）報價與前收盤價，計算漲跌幅
        僅供 Dashboard 顯示提醒，不影響任何排序/評分
        """
        try:
            fi = yf.Ticker(ticker).fast_info
            last = fi.get("lastPrice") or fi.get("last_price")
            prev = (fi.get("previousClose") or fi.get("regularMarketPreviousClose")
                    or fi.get("previous_close"))
            if not last or not prev:
                return None
            return {
                "last_price":  round(float(last), 2),
                "prev_close":  round(float(prev), 2),
                "change_pct":  round((float(last) - float(prev)) / float(prev) * 100, 2),
            }
        except Exception as e:
            logger.warning(f"{ticker} pre-market 查詢失敗: {e}")
            return None

    def get_next_earnings(self, ticker: str):
        """回傳下一次財報日期（pd.Timestamp）或 None"""
        try:
            t   = yf.Ticker(ticker)
            cal = t.calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
            elif cal is not None and not cal.empty:
                dates = cal.get("Earnings Date", [])
            else:
                dates = []
            if len(dates) > 0:
                return pd.Timestamp(dates[0])
        except Exception:
            pass
        return None