import yfinance as yf
import requests
import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime, timedelta
from config.settings import POLYGON_API_KEY

logger = logging.getLogger(__name__)

# 模擬瀏覽器 User-Agent，避免 Yahoo 封鎖 GitHub Actions IP
BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}


def _make_yf_session() -> requests.Session:
    """建立帶瀏覽器 header 的 requests session"""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    return session


class DataFetcher:
    """多源數據抓取，自動 fallback，解決數據缺失問題"""

    def __init__(self):
        self.polygon_key = POLYGON_API_KEY
        self.cache = {}
        self._session = _make_yf_session()

    # ─── 公開接口 ────────────────────────────────────────────

    def get_ohlcv(self, ticker: str, days: int = 365) -> pd.DataFrame | None:
        cache_key = f"{ticker}_{days}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        sources = [self._from_polygon, self._from_yfinance]
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
        """抓取股票基本信息，用瀏覽器 session 避免 429"""
        for attempt in range(3):
            try:
                t    = yf.Ticker(ticker, session=self._session)
                info = t.info
                time.sleep(0.5)
                return {
                    "market_cap":    info.get("marketCap", 0),
                    "sector":        info.get("sector", "Unknown"),
                    "industry":      info.get("industry", "Unknown"),
                    "beta":          info.get("beta", 1.0),
                    "short_name":    info.get("shortName", ticker),
                    "earnings_date": self._get_next_earnings(ticker),
                }
            except Exception as e:
                err = str(e)
                if "429" in err:
                    wait = (attempt + 1) * 20
                    logger.warning(f"{ticker} info 429，等待 {wait}s...")
                    time.sleep(wait)
                    # 重建 session，換新連接
                    self._session = _make_yf_session()
                elif "401" in err or "Unauthorized" in err:
                    logger.warning(f"{ticker} info 401，跳過")
                    break
                else:
                    logger.warning(f"{ticker} info 失敗: {e}")
                    break
        return {}

    def get_financials(self, ticker: str) -> dict:
        """抓取基本面數據，用瀏覽器 session"""
        for attempt in range(3):
            try:
                t      = yf.Ticker(ticker, session=self._session)
                income = t.quarterly_financials
                info   = t.info
                time.sleep(0.5)

                eps_list = []
                rev_list = []

                if income is not None and not income.empty:
                    if "Net Income" in income.index:
                        eps_list = income.loc["Net Income"].dropna().tolist()[:4]
                    if "Total Revenue" in income.index:
                        rev_list = income.loc["Total Revenue"].dropna().tolist()[:4]

                return {
                    "eps_quarters":         eps_list,
                    "revenue_quarters":     rev_list,
                    "gross_margin":         info.get("grossMargins", 0),
                    "institutional_pct":    info.get("institutionPercent", 0),
                    "institutional_change": info.get("heldPercentInstitutions", 0),
                    "forward_pe":           info.get("forwardPE", 0),
                    "peg_ratio":            info.get("pegRatio", 0),
                }
            except Exception as e:
                err = str(e)
                if "429" in err:
                    wait = (attempt + 1) * 20
                    logger.warning(f"{ticker} financials 429，等待 {wait}s...")
                    time.sleep(wait)
                    self._session = _make_yf_session()
                else:
                    logger.warning(f"{ticker} financials 失敗: {e}")
                    break
        return {}

    # ─── 私有方法 ─────────────────────────────────────────────

    def _from_polygon(self, ticker: str, days: int) -> pd.DataFrame:
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
        """yfinance 備用，用瀏覽器 session"""
        period = f"{days}d" if days <= 729 else "2y"
        df = yf.download(
            ticker,
            period=period,
            auto_adjust=True,
            progress=False,
            session=self._session,
        )
        if df.empty:
            raise ValueError(f"yfinance 無數據: {ticker}")
        df.columns = [c.lower() for c in df.columns]
        return df

    def _validate(self, df) -> bool:
        if df is None or df.empty:
            return False
        if len(df) < 60:
            return False
        if df["close"].isnull().mean() > 0.05:
            return False
        if (df["close"] <= 0).any():
            return False
        return True

    def _get_next_earnings(self, ticker: str):
        try:
            t   = yf.Ticker(ticker, session=self._session)
            cal = t.calendar
            if cal is not None and not cal.empty:
                dates = cal.get("Earnings Date", [])
                if len(dates) > 0:
                    return pd.Timestamp(dates[0])
        except Exception:
            pass
        return None
