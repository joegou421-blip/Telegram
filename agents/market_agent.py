import requests
import pandas as pd
import numpy as np
import logging
from data.fetcher import DataFetcher
from data.indicators import add_all_indicators
from config.settings import (
    MARKET_GATE, AI_MODEL_FAST, OPENROUTER_API_KEY, FTD_MIN_GAIN, FTD_MIN_DAY,
    DISTRIBUTION_DAYS_LOOKBACK, DISTRIBUTION_DAYS_THRESHOLD, DISTRIBUTION_DAY_DECLINE_PCT,
)

logger = logging.getLogger(__name__)

GATE_GREEN  = "green"
GATE_YELLOW = "yellow"
GATE_RED    = "red"


class MarketAgent:

    def __init__(self):
        self.fetcher = DataFetcher()

    def analyze(self) -> dict:
        spy_data  = self._analyze_index("SPY")
        qqq_data  = self._analyze_index("QQQ")
        vix_data  = self._get_vix()
        breadth   = self._calc_market_breadth()
        ftd       = self._detect_follow_through_day()
        news      = self._ai_news_analysis()

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

        # 新聞升降燈號
        if news["sentiment"] == "negative" and gate == GATE_GREEN:
            gate = GATE_YELLOW
        elif news["sentiment"] == "positive" and gate == GATE_YELLOW:
            gate = GATE_GREEN

        # Follow-Through Day 可以把紅燈升為黃燈（但不升為綠燈）
        if ftd["detected"] and gate == GATE_RED:
            gate = GATE_YELLOW
            logger.info("Follow-Through Day 偵測到，紅燈升為黃燈")

        # Distribution Days：累積到門檻時，提早把綠燈降為黃燈（比 EMA 風控更早示警）
        dist_days = self._calc_distribution_days()
        if dist_days >= DISTRIBUTION_DAYS_THRESHOLD and gate == GATE_GREEN:
            gate = GATE_YELLOW
            logger.info(f"Distribution Days {dist_days}/{DISTRIBUTION_DAYS_LOOKBACK}，綠燈降為黃燈")

        regime = self._classify_regime(gate, breadth, vix_data)

        return {
            "gate":              gate,
            "regime":            regime,
            "distribution_days": dist_days,
            "summary": self._build_summary(gate, spy_data, qqq_data, vix_data, breadth, ftd, news, dist_days),
            "ftd":     ftd,
            "details": {
                "spy":     spy_data,
                "qqq":     qqq_data,
                "vix":     vix_data,
                "breadth": breadth,
                "ftd":     ftd,
                "news":    news,
            },
        }

    def _classify_regime(self, gate: str, breadth: dict, vix: dict) -> str:
        """
        判斷市場是否處於「全面性放量多頭」(bull_hot)：
        綠燈 + 市場寬度 ≥75% + VIX <17 → bull_hot（VDU 門檻放寬至 80%）
        其他情況 → normal（VDU 門檻維持 65%）

        門檻依過去400個交易日校準：VIX 中位數約17.5，
        原 <15 門檻僅觸發6%的日子（幾乎死代碼）；
        改為 <17 後觸發約28.5%的日子，且集中在大盤綠燈、波動平穩期間。
        """
        breadth_pct = breadth.get("pct", 0.5)
        vix_val     = vix.get("value", 20)
        if gate == GATE_GREEN and breadth_pct >= 0.75 and vix_val < 17:
            return "bull_hot"
        return "normal"

    def _analyze_index(self, ticker) -> dict:
        df = self.fetcher.get_ohlcv(ticker, days=200)
        if df is None:
            return {"signal": GATE_YELLOW, "notes": f"無法取得 {ticker} 數據"}
        df   = add_all_indicators(df)
        last = df.iloc[-1]
        above_ema20 = last["close"] > last["ema20"]
        above_ema50 = last["close"] > last["ema50"]
        if above_ema20 and above_ema50:
            signal = GATE_GREEN
        elif above_ema50:
            signal = GATE_YELLOW
        else:
            signal = GATE_RED
        return {
            "signal":      signal,
            "price":       round(last["close"], 2),
            "ema20":       round(last["ema20"], 2),
            "ema50":       round(last["ema50"], 2),
            "above_ema20": above_ema20,
            "above_ema50": above_ema50,
        }

    def _get_vix(self) -> dict:
        try:
            df = self.fetcher.get_ohlcv("^VIX", days=30)
            if df is None:
                return {"signal": GATE_YELLOW, "value": 20}
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

    def _calc_market_breadth(self) -> dict:
        sample = ["QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI", "XLC"]
        above = total = 0
        for ticker in sample:
            df = self.fetcher.get_ohlcv(ticker, days=220)
            if df is not None and len(df) > 200:
                last   = df["close"].iloc[-1]
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

    def _calc_distribution_days(self) -> int:
        """
        過去 DISTRIBUTION_DAYS_LOOKBACK 個交易日內，SPY 收盤跌幅超過
        DISTRIBUTION_DAY_DECLINE_PCT 且成交量高於前一天的天數
        """
        try:
            df = self.fetcher.get_ohlcv("SPY", days=60)
            if df is None or len(df) < DISTRIBUTION_DAYS_LOOKBACK + 1:
                return 0

            recent  = df.tail(DISTRIBUTION_DAYS_LOOKBACK + 1)
            closes  = recent["close"].values
            volumes = recent["volume"].values

            count = 0
            for i in range(1, len(recent)):
                pct_change = (closes[i] - closes[i - 1]) / closes[i - 1]
                if pct_change < -DISTRIBUTION_DAY_DECLINE_PCT and volumes[i] > volumes[i - 1]:
                    count += 1
            return count
        except Exception as e:
            logger.warning(f"Distribution Days 計算失敗: {e}")
            return 0

    def _detect_follow_through_day(self) -> dict:
        """
        Follow-Through Day 判斷：
        大盤修正後反彈第4天起，單日大漲 ≥1.7% 且成交量放大
        """
        try:
            df = self.fetcher.get_ohlcv("SPY", days=60)
            if df is None or len(df) < 20:
                return {"detected": False, "notes": "數據不足"}

            closes  = df["close"].values
            volumes = df["volume"].values
            n       = len(closes)

            # 找最近的低點（修正底部）
            recent_low_idx = np.argmin(closes[-20:]) + (n - 20)
            days_since_low = n - 1 - recent_low_idx

            if days_since_low < FTD_MIN_DAY:
                return {"detected": False, "notes": f"反彈僅第 {days_since_low} 天，未到第 {FTD_MIN_DAY} 天"}

            # 今天的漲幅和成交量
            today_gain   = (closes[-1] - closes[-2]) / closes[-2]
            avg_vol      = np.mean(volumes[-20:-1])
            today_vol    = volumes[-1]
            vol_increase = today_vol > avg_vol

            if today_gain >= FTD_MIN_GAIN and vol_increase:
                return {
                    "detected": True,
                    "notes":    f"FTD 確認！反彈第 {days_since_low} 天，今日漲 {today_gain*100:.1f}%，成交量放大",
                    "gain":     round(today_gain * 100, 1),
                    "day":      days_since_low,
                }
            else:
                return {
                    "detected": False,
                    "notes":    f"反彈第 {days_since_low} 天，今日漲 {today_gain*100:.1f}%（未達標）",
                }
        except Exception as e:
            logger.warning(f"FTD 判斷失敗: {e}")
            return {"detected": False, "notes": "判斷失敗"}

    def _ai_news_analysis(self) -> dict:
        if not OPENROUTER_API_KEY:
            return {"sentiment": "neutral", "summary": "未設定 AI API"}
        try:
            prompt = (
                "你是資深股市分析師。分析今天美股市場宏觀環境：\n"
                "1. 聯儲局最新動態\n2. 重大地緣政治風險\n3. 整體市場情緒\n\n"
                "最後一行只寫：SENTIMENT: positive/neutral/negative"
            )
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://swing-trade-agent.github.io",
                    "X-Title":       "Swing Trade Agent",
                },
                json={
                    "model":      AI_MODEL_FAST,
                    "max_tokens": 200,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            resp.raise_for_status()
            content   = resp.json()["choices"][0]["message"]["content"].strip()
            sentiment = "neutral"
            for line in content.split("\n"):
                if "SENTIMENT:" in line:
                    s = line.split("SENTIMENT:")[-1].strip().lower()
                    if s in ("positive", "negative", "neutral"):
                        sentiment = s
            summary = "\n".join(l for l in content.split("\n") if "SENTIMENT:" not in l).strip()
            return {"sentiment": sentiment, "summary": summary}
        except Exception as e:
            logger.warning(f"AI 新聞分析失敗: {e}")
            return {"sentiment": "neutral", "summary": "分析失敗"}

    def _build_summary(self, gate, spy, qqq, vix, breadth, ftd, news, dist_days=0) -> str:
        gate_str = {"green": "綠燈", "yellow": "黃燈", "red": "紅燈"}[gate]
        spy_str  = (
            "EMA20/50 之上" if spy.get("above_ema20") and spy.get("above_ema50")
            else "EMA50 之上" if spy.get("above_ema50")
            else "EMA50 之下"
        )
        ftd_str = f" · 🎯 FTD 確認！" if ftd.get("detected") else ""
        return (
            f"大盤{gate_str} · SPY {spy_str} · "
            f"VIX {vix.get('value', 'N/A')} · "
            f"市場寬度 {int(breadth.get('pct', 0.5)*100)}% · "
            f"Distribution Days {dist_days}/{DISTRIBUTION_DAYS_LOOKBACK}"
            f"{ftd_str}\n{news.get('summary', '')}"
        )
