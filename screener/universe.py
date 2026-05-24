import yfinance as yf
import pandas as pd
import logging
from config.settings import SCREENER

logger = logging.getLogger(__name__)

# S&P 500 + Nasdaq 100 主要成分股（可定期更新）
BASE_UNIVERSE = [
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","ORCL","AMD",
    "CRM","ADBE","QCOM","TXN","AMAT","LRCX","KLAC","SNDK","MU","MRVL",
    "NOW","SNOW","DDOG","PANW","CRWD","ZS","NET","FTNT","OKTA","S",
    "AXON","DECK","ONON","LULU","NKE","TJX","HD","LOW","COST","WMT",
    "LLY","UNH","ABBV","ABT","TMO","DHR","ISRG","VRTX","REGN","MRNA",
    "GS","MS","JPM","V","MA","PYPL","SQ","COIN","HOOD",
    "TSLA","F","GM","TM","RIVN","LCID",
    "XOM","CVX","EOG","PXD","SLB","HAL",
    "CAT","DE","HON","RTX","LMT","NOC","GE","ETN",
    "NEE","DUK","SO","AEP",
    "AMT","PLD","EQIX","CCI",
    "META","DIS","NFLX","SPOT","RBLX","U",
    "UBER","LYFT","ABNB","BKNG","EXPE",
    "MELI","SE","GRAB","SHOP",
]


def get_candidate_tickers() -> list:
    """
    第一層量化篩選：從基礎宇宙篩出符合條件的候選股
    純數學，不用 AI，速度快
    """
    logger.info(f"開始篩選，宇宙大小: {len(BASE_UNIVERSE)}")
    passed = []

    for ticker in BASE_UNIVERSE:
        try:
            if _passes_screen(ticker):
                passed.append(ticker)
        except Exception as e:
            logger.debug(f"{ticker} 篩選失敗: {e}")

    logger.info(f"通過篩選: {len(passed)} 隻")
    return passed


def _passes_screen(ticker: str) -> bool:
    t    = yf.Ticker(ticker)
    info = t.info
    hist = t.history(period="1y", auto_adjust=True)

    if hist.empty or len(hist) < 200:
        return False

    close   = hist["Close"]
    volume  = hist["Volume"]
    price   = close.iloc[-1]

    # 價格門檻
    if price < SCREENER["min_price"]:
        return False

    # 市值
    mkt_cap = info.get("marketCap", 0)
    if mkt_cap < SCREENER["min_market_cap"]:
        return False

    # 月成交額（近20天均量 × 價格 × 20天）
    avg_vol_20     = volume.tail(20).mean()
    monthly_dollar = avg_vol_20 * price * 20
    if monthly_dollar < SCREENER["min_monthly_volume"]:
        return False

    # Price > SMA 200
    sma200 = close.rolling(200).mean().iloc[-1]
    if price < sma200:
        return False

    # SMA 50 > SMA 200（趨勢排列）
    sma50 = close.rolling(50).mean().iloc[-1]
    if sma50 < sma200:
        return False

    # Price > SMA 50
    if price < sma50:
        return False

    # 距 52 週高點 < 25%
    high_52w = close.tail(252).max()
    if (high_52w - price) / high_52w > SCREENER["max_from_52w_high"]:
        return False

    return True
