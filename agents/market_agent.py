import requests
import pandas as pd
import logging
from data.fetcher import DataFetcher
from data.indicators import add_all_indicators
from config.settings import MARKET_GATE, AI_MODEL, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

GATE_GREEN  = "green"
GATE_YELLOW = "yellow"
GATE_RED    = "red"


class MarketAgent:

    def __init__(self):
        self.fetcher = DataFetcher()

    def analyze(self) -> dict:
        """分析大盤環境，輸出通行證"""

        spy_data = self._analyze_index("SPY")
        qqq_data = self._analyze_index("QQQ")
        vix_data = self._get_vix()
        breadth  = self._calc_market_breadth()
        news     = self._ai_news_analysis()

        # 多數投票決定燈號
        votes = [
            spy_data["signal"],
            qqq_data["signal"],
            vix_data["signal"],
            breadth["signal"],
        ]
        red_count    = votes.count(GATE_RED)
        yellow_count = votes.count(GATE_YELLOW)

        if red_count >= 2:
            gate = GATE_RED
        elif red_count >= 1 or yellow_count >= 2:
            gate = GATE_YELLOW
        else:
            gate = GATE_GREEN

        # 新聞可以升降一級
        if news["sentiment"] == "negative" and gate == GATE_GREEN:
            gate = GATE_YELLOW
        elif news["sentiment"] == "positive" and gate == GATE_YELLOW:
            gate = GATE_GREEN

        return {
            "gate":    gate,
            "summary": self._build_summary(gate, spy_data, qqq_data, vix_data, breadth, news),
            "details": {
                "spy":     spy_data,
                "qqq":     qqq_data,
                "vix":     vix_data,
                "breadth": breadth,
                "news":    news,
            },
        }

    # ─── 指數分析 ────────────────────────────────────────────

    def _analyze_index(self, ticker: str) -> dict:
        df = self.fetcher.get_ohlcv(ticker, days=200)
        if df is None:
            return {"signal": GATE_YELLOW, "notes": f"無法取得 {ticker} 數據"}

        df = add_all_indicators(df)
        last = df.iloc[-1]

        above_ema20  = last["close"] > last["ema20"]
        above_ema50  = last["close"] > last["ema50"]

        if above_ema20 and above_ema50:
            signal = GATE_GREEN
        elif above_ema50:
            signal = GATE_YELLOW
        else:
            signal = GATE_RED

        return {
            "signal":       signal,
            "price":        round(last["close"], 2),
            "ema20":        round(last["ema20"], 2),
            "ema50":        round(last["ema50"], 2),
            "above_ema20":  above_ema20,
            "above_ema50":  above_ema50,
        }

    # ─── VIX ────────────────────────────────────────────────

    def _get_vix(self) -> dict:
        try:
            df = self.fetcher.get_ohlcv("^VIX", days=30)
            if df is None:
                return {"signal": GATE_YELLOW, "value": 20, "notes": "無法取得 VIX"}
            vix_val = df["close"].iloc[-1]
            if vix_val > MARKET_GATE["vix_red"]:
                signal = GATE_RED
            elif vix_val > MARKET_GATE["vix_yellow"]:
                signal = GATE_YELLOW
            else:
                signal = GATE_GREEN
            return {"signal": signal, "value": round(vix_val, 1)}
        except Exception as e:
            logger.warning(f"VIX 取得失敗: {e}")
            return {"signal": GATE_YELLOW, "value": 20}

    # ─── 市場寬度（用 SPY 成分股近似）──────────────────────

    def _calc_market_breadth(self) -> dict:
        # 用幾個主要 ETF 的 SMA200 狀態作為寬度近似
        sample = ["QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI", "XLC"]
        above  = 0
        total  = 0
        for ticker in sample:
            df = self.fetcher.get_ohlcv(ticker, days=220)
            if df is not None and len(df) > 200:
                last  = df["close"].iloc[-1]
                sma200 = df["close"].rolling(200).mean().iloc[-1]
                if last > sma200:
                    above += 1
                total += 1

        if total == 0:
            return {"signal": GATE_YELLOW, "pct": 0.5}

        pct = above / total
        if pct < MARKET_GATE["breadth_red"]:
            signal = GATE_RED
        elif pct < MARKET_GATE["breadth_yellow"]:
            signal = GATE_YELLOW
        else:
            signal = GATE_GREEN

        return {"signal": signal, "pct": round(pct, 2), "above": above, "total": total}

    # ─── AI 新聞情緒分析 ────────────────────────────────────

    def _ai_news_analysis(self) -> dict:
        if not OPENROUTER_API_KEY:
            return {"sentiment": "neutral", "summary": "未設定 AI API"}

        try:
            prompt = (
                "你是資深股市分析師。根據你的知識，"
                "分析今天美股市場的宏觀環境，包括：\n"
                "1. 聯儲局最新動態\n"
                "2. 重要地緣政治風險\n"
                "3. 整體市場情緒\n\n"
                "最後給出一個判斷：positive / neutral / negative\n"
                "格式：先說 2 句分析，最後一行只寫 SENTIMENT: positive/neutral/negative"
            )
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":      AI_MODEL,
                    "max_tokens": 200,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

            sentiment = "neutral"
            for line in content.split("\n"):
                if "SENTIMENT:" in line:
                    s = line.split("SENTIMENT:")[-1].strip().lower()
                    if s in ("positive", "negative", "neutral"):
                        sentiment = s

            summary = "\n".join(
                l for l in content.split("\n") if "SENTIMENT:" not in l
            ).strip()

            return {"sentiment": sentiment, "summary": summary}

        except Exception as e:
            logger.warning(f"AI 新聞分析失敗: {e}")
            return {"sentiment": "neutral", "summary": "分析失敗"}

    # ─── 總結 ────────────────────────────────────────────────

    def _build_summary(self, gate, spy, qqq, vix, breadth, news) -> str:
        gate_str = {"green": "綠燈", "yellow": "黃燈", "red": "紅燈"}[gate]
        spy_str  = "EMA20/50 之上" if spy.get("above_ema20") and spy.get("above_ema50") else \
                   "EMA50 之上" if spy.get("above_ema50") else "EMA50 之下"
        return (
            f"大盤{gate_str} · "
            f"SPY {spy_str} · "
            f"VIX {vix.get('value', 'N/A')} · "
            f"市場寬度 {int(breadth.get('pct', 0.5)*100)}%\n"
            f"{news.get('summary', '')}"
        )
