import yfinance as yf
import pandas as pd
import time
import random
import logging
from config.settings import SCREENER, POLYGON_API_KEY
import requests

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
    "NEE","DUK","AMT","PLD","EQIX",
    "DIS","NFLX","SPOT","UBER","ABNB","BKNG",
    "MELI","SHOP",
]

BATCH_SIZE = 10   # 每批幾隻
BATCH_DELAY = 8   # 批次之間等幾秒


def get_candidate_tickers() -> list:
    logger.info(f"開始篩選，宇宙大小: {len(BASE_UNIVERSE)}")

    # Step 1：分批 batch download，純價格篩選
    price_passed = _batch_price_screen()
    logger.info(f"價格/SMA篩選通過: {len(price_passed)} 隻")

    if not price_passed:
        return []

    # Step 2：只對通過的股票查市值（大幅減少請求次數）
    final = _market_cap_screen(price_passed)
    logger.info(f"最終通過: {len(final)} 隻")
    return final


def _batch_price_screen() -> list:
    """分批下載，每批加 delay，避免被封"""
    passed = []
    batches = [
        BASE_UNIVERSE[i:i + BATCH_SIZE]
        for i in range(0, len(BASE_UNIVERSE), BATCH_SIZE)
    ]

    for i, batch in enumerate(batches):
        logger.info(f"下載第 {i+1}/{len(batches)} 批: {batch}")
        try:
            # 加隨機 delay，避免固定頻率被識別
            if i > 0:
                delay = BATCH_DELAY + random.uniform(0, 3)
                logger.info(f"等待 {delay:.1f}s...")
                time.sleep(delay)

            raw = yf.download(
                tickers=" ".join(batch),
                period="1y",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=False,  # 關閉多線程，減少並發請求
            )

            if raw.empty:
                logger.warning(f"第 {i+1} 批下載失敗，跳過")
                continue

            for ticker in batch:
                try:
                    # 單隻股票時 columns 結構不同
                    if len(batch) == 1:
                        df = raw
                    else:
                        if ticker not in raw.columns.get_level_values(0):
                            continue
                        df = raw[ticker]

                    df = df.dropna()
                    if _passes_price_filter(df):
                        passed.append(ticker)

                except Exception as e:
                    logger.debug(f"{ticker} 篩選異常: {e}")

        except Exception as e:
            logger.warning(f"第 {i+1} 批下載異常: {e}")

    return passed


def _passes_price_filter(df: pd.DataFrame) -> bool:
    """純價格、SMA、成交量篩選，不需要額外請求"""
    if len(df) < 200:
        return False

    close  = df["Close"] if "Close" in df.columns else df["close"]
    volume = df["Volume"] if "Volume" in df.columns else df["volume"]
    price  = float(close.iloc[-1])

    if price < SCREENER["min_price"]:
        return False

    # 月成交額
    avg_vol_20     = volume.tail(20).mean()
    monthly_dollar = avg_vol_20 * price * 20
    if monthly_dollar < SCREENER["min_monthly_volume"]:
        return False

    # SMA 排列
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


def _market_cap_screen(tickers: list) -> list:
    """
    只對價格篩選通過的股票查市值
    請求次數大幅減少（從 80 次降到 15-20 次）
    """
    passed = []
    for ticker in tickers:
        try:
            # 先試 Polygon（最穩定）
            mkt_cap = _get_market_cap_polygon(ticker)

            # Polygon 拿不到才用 yfinance
            if mkt_cap is None:
                time.sleep(1)
                mkt_cap = _get_market_cap_yfinance(ticker)

            if mkt_cap and mkt_cap >= SCREENER["min_market_cap"]:
                passed.append(ticker)
                logger.info(f"  ✓ {ticker} 市值 ${mkt_cap/1e9:.1f}B")
            else:
                logger.debug(f"  ✗ {ticker} 市值不足")

            time.sleep(0.5)

        except Exception as e:
            logger.debug(f"{ticker} 市值查詢失敗: {e}")
            # 查不到市值就保留，讓後面的 agent 處理
            passed.append(ticker)

    return passed


def _get_market_cap_polygon(ticker: str):
    """用 Polygon 查市值"""
    if not POLYGON_API_KEY:
        return None
    try:
        url  = f"https://api.polygon.io/v3/reference/tickers/{ticker}?apiKey={POLYGON_API_KEY}"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("results", {}).get("market_cap")
        if resp.status_code == 429:
            time.sleep(15)
    except Exception:
        pass
    return None


def _get_market_cap_yfinance(ticker: str):
    """用 yfinance 查市值，備用"""
    try:
        info = yf.Ticker(ticker).fast_info
        return getattr(info, "market_cap", None)
    except Exception:
        return None
