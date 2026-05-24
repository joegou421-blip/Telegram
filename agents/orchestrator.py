import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from config.settings import SCORE_THRESHOLD
from agents.technical_agent    import TechnicalAgent
from agents.fundamental_agent  import FundamentalAgent
from agents.market_agent       import MarketAgent
from data.fetcher              import DataFetcher

logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self):
        self.tech_agent  = TechnicalAgent()
        self.fund_agent  = FundamentalAgent()
        self.mkt_agent   = MarketAgent()
        self.fetcher     = DataFetcher()

    def run_daily_scan(self, tickers: list) -> dict:
        """每日掃描主流程"""
        logger.info(f"開始掃描 {len(tickers)} 隻候選股")

        # 先取得大盤狀態
        market = self.mkt_agent.analyze()
        logger.info(f"大盤通行證: {market['gate']}")

        # 紅燈直接不分析個股
        if market["gate"] == "red":
            return {
                "market":     market,
                "candidates": [],
                "skipped_reason": "大盤紅燈，暫停個股分析",
            }

        # 抓 SPY 數據（RS Line 需要）
        spy_df = self.fetcher.get_spy_ohlcv()

        # 並行分析所有候選股
        candidates = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._analyze_one, ticker, spy_df): ticker
                for ticker in tickers
            }
            for future, ticker in futures.items():
                try:
                    result = future.result(timeout=60)
                    if result:
                        candidates.append(result)
                except Exception as e:
                    logger.error(f"{ticker} 分析失敗: {e}")

        # 過濾和排序
        passed = [c for c in candidates if self._passes_threshold(c, market["gate"])]
        passed.sort(key=lambda x: x["composite_score"], reverse=True)

        # 黃燈只推最高分的
        if market["gate"] == "yellow":
            passed = [c for c in passed if c["composite_score"] >= 80]

        logger.info(f"通過門檻: {len(passed)} 隻")
        return {
            "market":     market,
            "candidates": passed,
        }

    def analyze_single(self, ticker: str) -> dict:
        """主動查詢單一股票（你在 Telegram 問的時候用這個）"""
        spy_df = self.fetcher.get_spy_ohlcv()
        result = self._analyze_one(ticker, spy_df)
        market = self.mkt_agent.analyze()
        if result:
            result["market"] = market
        return result

    # ─── 分析單一股票 ─────────────────────────────────────────

    def _analyze_one(self, ticker: str, spy_df) -> dict | None:
        try:
            # 抓數據
            df      = self.fetcher.get_ohlcv(ticker)
            info    = self.fetcher.get_info(ticker)
            fin     = self.fetcher.get_financials(ticker)

            if df is None:
                logger.warning(f"{ticker}: 無法取得數據")
                return None

            # 三個 Agent 分析
            tech = self.tech_agent.analyze(ticker, df, spy_df)
            fund = self.fund_agent.analyze(ticker, info, fin)

            # Earnings 硬性排除
            if fund.get("disqualified"):
                logger.info(f"{ticker} 排除: {fund['reason']}")
                return None

            # ATR 風險硬性排除
            if tech["details"]["atr_risk_pct"] > 8:
                logger.info(f"{ticker} 排除: ATR 風險 {tech['details']['atr_risk_pct']}% 太大")
                return None

            composite = self._calc_composite(tech["total"], fund["total"])

            return {
                "ticker":          ticker,
                "short_name":      info.get("short_name", ticker),
                "sector":          info.get("sector", "Unknown"),
                "price":           tech["details"]["entry_price"],
                "tech_score":      tech["total"],
                "fund_score":      fund["total"],
                "composite_score": composite,
                "tech":            tech,
                "fund":            fund,
            }

        except Exception as e:
            logger.error(f"{ticker} 分析異常: {e}")
            return None

    def _passes_threshold(self, result: dict, gate: str) -> bool:
        if result["tech_score"] < SCORE_THRESHOLD["technical"]:
            return False
        if result["fund_score"] < SCORE_THRESHOLD["fundamental"]:
            return False
        return True

    def _calc_composite(self, tech_score: float, fund_score: float) -> float:
        # 技術面佔 55%，基本面佔 45%
        return round(tech_score * 0.55 + fund_score * 0.45)
