import yfinance as yf
import pandas as pd
import time
import random
import logging
from config.settings import SCREENER, POLYGON_API_KEY
import requests

logger = logging.getLogger(__name__)

# S&P 500 + Nasdaq 100 完整名單
SP500_NASDAQ100 = [
    # 科技
    "AAPL","MSFT","NVDA","GOOGL","GOOG","META","AMZN","TSLA","AVGO","ORCL",
    "AMD","CRM","ADBE","QCOM","TXN","AMAT","LRCX","KLAC","MRVL","SNDK",
    "NOW","SNOW","DDOG","PANW","CRWD","ZS","NET","FTNT","OKTA","S",
    "PLTR","AXON","HUBS","TEAM","MDB","CFLT","GTLB","PATH","ZM","DOCU",
    "UBER","LYFT","ABNB","BKNG","EXPE","AIRB",
    "NFLX","DIS","CMCSA","WBD","PARA","FOX","FOXA",
    "SPOT","RBLX","U","EA","TTWO","ATVI",
    # 半導體
    "INTC","MU","ON","SWKS","QRVO","MPWR","ENTG","MKSI","COHU","UCTT",
    "WOLF","AEHR","ACLS","ONTO","FORM","ICHR","CAMT","RMBS","SLAB",
    # 金融
    "JPM","BAC","WFC","GS","MS","C","USB","PNC","TFC","COF",
    "AXP","V","MA","PYPL","SQ","COIN","HOOD","SOFI","AFRM",
    "BLK","SCHW","BX","APO","KKR","CG","ARES",
    "CB","MET","PRU","AFL","ALL","AIG","HIG","TRV",
    # 醫療
    "LLY","UNH","JNJ","ABBV","MRK","PFE","BMY","AMGN","GILD","REGN",
    "VRTX","BIIB","INCY","ALNY","MRNA","BNTX","NVAX",
    "ABT","TMO","DHR","A","IDXX","IQV","CRL","MEDP",
    "ISRG","EW","SYK","BSX","MDT","ZBH","HOLX",
    "CVS","CI","HUM","CNC","MOH","ELV",
    # 消費
    "AMZN","WMT","COST","TGT","HD","LOW","ORLY","AZO","AAP",
    "MCD","SBUX","CMG","YUM","DPZ","QSR","JACK",
    "NKE","LULU","ONON","DECK","CROX","SKX","UA",
    "TJX","ROST","BURL","GPS","ANF","AEO",
    "PG","KO","PEP","MDLZ","GIS","K","CPB","CAG","SJM",
    "EL","ULTA","COTY","REV",
    # 工業
    "CAT","DE","HON","MMM","EMR","ROK","PH","ITW","GE","ETN",
    "RTX","LMT","NOC","GD","BA","HII","TDG","HEI","AXON",
    "UPS","FDX","XPO","SAIA","ODFL","CHRW","EXPD","JBHT",
    "URI","RSG","WM","CTAS","FAST","GWW","MSC","WSO",
    # 能源
    "XOM","CVX","COP","EOG","PXD","DVN","FANG","MRO","APA","HES",
    "SLB","HAL","BKR","OIS","WHD","NRGY",
    "PSX","VLO","MPC","DK","PBF",
    "OKE","WMB","KMI","ET","EPD","MMP",
    # 材料 / 基礎
    "LIN","APD","SHW","ECL","PPG","RPM","IFF","CE","EMN","HUN",
    "NUE","STLD","CLF","X","AA","FCX","NEM","AEM","GOLD","KGC",
    "DD","DOW","LYB","WLK","OLN","CC",
    # REITs / 公用事業
    "AMT","PLD","EQIX","CCI","SBAC","DLR","ARE","BXP","VNO","SLG",
    "NEE","DUK","SO","AEP","EXC","PCG","ED","D","FE","ETR",
    "AWK","WTR","CWT","SJW",
    # 電信
    "T","VZ","TMUS","LBRDK","CHTR","CABO","WOW",
    # 其他成長股
    "MELI","SE","GRAB","SHOP","ETSY","PINS","SNAP","TWTR",
    "DKNG","MGM","WYNN","LVS","CZR","PENN",
    "RIVN","LCID","NIO","LI","XPEV","NKLA",
    "SPCE","ASTR","RKT","ACHR","JOBY",
]

# 去重
SP500_NASDAQ100 = list(dict.fromkeys(SP500_NASDAQ100))

BATCH_SIZE  = 15
BATCH_DELAY = 10


def get_candidate_tickers() -> list:
    logger.info(f"開始篩選，宇宙大小: {len(SP500_NASDAQ100)}")

    price_passed = _batch_price_screen()
    logger.info(f"價格/SMA 篩選通過: {len(price_passed)} 隻")

    if not price_passed:
        return []

    final = _market_cap_screen(price_passed)
    logger.info(f"最終通過: {len(final)} 隻")
    return final


def _batch_price_screen() -> list:
    passed  = []
    batches = [
        SP500_NASDAQ100[i:i + BATCH_SIZE]
        for i in range(0, len(SP500_NASDAQ100), BATCH_SIZE)
    ]

    for i, batch in enumerate(batches):
        logger.info(f"下載第 {i+1}/{len(batches)} 批: {batch[:5]}...")
        try:
            if i > 0:
                delay = BATCH_DELAY + random.uniform(0, 4)
                logger.info(f"等待 {delay:.1f}s...")
                time.sleep(delay)

            raw = yf.download(
                tickers  = " ".join(batch),
                period   = "1y",
                auto_adjust = True,
                progress = False,
                group_by = "ticker",
                threads  = False,
            )

            if raw.empty:
                logger.warning(f"第 {i+1} 批下載失敗，跳過")
                continue

            for ticker in batch:
                try:
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
    if len(df) < 200:
        return False

    close  = df["Close"] if "Close" in df.columns else df["close"]
    volume = df["Volume"] if "Volume" in df.columns else df["volume"]
    price  = float(close.iloc[-1])

    if price < SCREENER["min_price"]:
        return False

    avg_vol_20     = volume.tail(20).mean()
    monthly_dollar = avg_vol_20 * price * 20
    if monthly_dollar < SCREENER["min_monthly_volume"]:
        return False

    sma200 = close.rolling(200).mean().iloc[-1]
    sma50  = close.rolling(50).mean().iloc[-1]

    if price < sma200:
        return False
    if sma50 < sma200:
        return False
    if price < sma50:
        return False

    high_52w = close.tail(252).max()
    if (high_52w - price) / high_52w > SCREENER["max_from_52w_high"]:
        return False

    return True


def _market_cap_screen(tickers: list) -> list:
    passed = []
    for ticker in tickers:
        try:
            mkt_cap = _get_market_cap_polygon(ticker)
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
            passed.append(ticker)

    return passed


def _get_market_cap_polygon(ticker: str):
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
    try:
        info = yf.Ticker(ticker).fast_info
        return getattr(info, "market_cap", None)
    except Exception:
        return None
