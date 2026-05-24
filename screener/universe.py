import yfinance as yf
import pandas as pd
import time
import logging
from config.settings import SCREENER

logger = logging.getLogger(__name__)

BASE_UNIVERSE = [
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","ORCL","AMD",
    "CRM","ADBE","QCOM","TXN","AMAT","LRCX","KLAC","MU","MRVL","SNDK",
    "NOW","SNOW","DDOG","PANW","CRWD","ZS","NET","FTNT","OKTA",
    "AXON","DECK","ONON","LULU","NKE","TJX","HD","LOW","COST","WMT",
    "LLY","UNH","ABBV","ABT","TMO","DHR","ISRG","VRTX","REGN",
    "GS","MS","JPM","V","MA","PYPL","COIN",
    "XOM","CVX","EOG","SLB",
    "CAT","DE","HON","RTX","LMT","GE","ETN",
    "NEE","DUK",
    "AMT","PLD","EQIX",
    "DIS","NFLX","SPOT","RBLX",
    "UBER","ABNB","BKNG",
    "MELI","SHOP",
]


def get_candidate_tickers() -> list:
    """
    第一層篩選：用 yfinance batch 下載減少請求，加 delay 避免限速
    """
    logger.info(f"開始篩選，宇宙大小: {len(BASE_UNIVERSE)}")

    # 用 batch 下載所有股票的歷史數據，一次請求搞定
    # 比逐個請求減少 90% 的 API 調用
    try:
        logger.info("批量下載歷史數據...")
        raw = yf.download(
            tickers=BASE_UNIVERSE,
            period="1y",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        logger.error(f"批量下載失敗: {e}")
        return []

    passed = []
    for ticker in BASE_UNIVERSE:
        try:
            if _passes_price_screen(ticker, raw):
                passed.append(ticker)
            time.sleep(0.1)  # 輕微 delay，避免後續 info 請求被限速
        except Exception as e:
            logger.debug(f"{ticker} 篩選失敗: {e}")

    logger.info(f"價格篩選通過: {len(passed)} 隻，開始市值篩選...")

    # 市值篩選單獨做，加 delay
    final = []
    for ticker in passed:
        try:
            if _passes_market_cap(ticker):
                final.append(ticker)
            time.sleep(0.5)  # 避免 429
        except Exception as e:
            logger.debug(f"{ticker} 市值篩選失敗: {e}")

    logger.info(f"最終通過篩選: {len(final)} 隻")
    return final


def _passes_price_screen(ticker: str, raw) -> bool:
    """純價格和成交量篩選，用 batch 數據，不需要額外請求"""
    try:
        # 取出這隻股票的數據
        if ticker in raw.columns.get_level_values(0):
            df = raw[ticker].dropna()
        else:
            return False

        if len(df) < 200:
            return False

        close  = df["Close"]
        volume = df["Volume"]
        price  = close.iloc[-1]

        if price < SCREENER["min_price"]:
            return False

        # 月成交額
        avg_vol_20     = volume.tail(20).mean()
        monthly_dollar = avg_vol_20 * price * 20
        if monthly_dollar < SCREENER["min_monthly_volume"]:
            return False

        # SMA 篩選
        sma200 = close.rolling(200).mean().iloc[-1]
        sma50  = close.rolling(50).mean().iloc[-1]

        if price < sma200:
            return False
        if sma50 < sma200:
            return False
        if price < sma50:
            return False

        # 距 52 週高點
        high_52w = close.tail(252).max()
        if (high_52w - price) / high_52w > SCREENER["max_from_52w_high"]:
            return False

        return True

    except Exception as e:
        logger.debug(f"{ticker} 價格篩選異常: {e}")
        return False


def _passes_market_cap(ticker: str) -> bool:
    """市值篩選，需要單獨請求（batch 沒有市值數據）"""
    for attempt in range(3):  # 最多重試 3 次
        try:
            info    = yf.Ticker(ticker).fast_info  # 用 fast_info，比 info 快很多
            mkt_cap = getattr(info, 'market_cap', 0) or 0
            return mkt_cap >= SCREENER["min_market_cap"]
        except Exception as e:
            if "429" in str(e):
                wait = (attempt + 1) * 10  # 10秒、20秒、30秒
                logger.warning(f"{ticker} 429限速，等待 {wait} 秒...")
                time.sleep(wait)
            else:
                return False
    return False
