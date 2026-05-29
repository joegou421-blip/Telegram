import logging
from concurrent.futures import ThreadPoolExecutor
from config.settings import SCORE_THRESHOLD
from agents.technical_agent   import TechnicalAgent
from agents.fundamental_agent import FundamentalAgent
from agents.market_agent      import MarketAgent
from data.fetcher             import DataFetcher
from watchlist.database       import Database

logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self):
        self.tech_agent  = TechnicalAgent()
        self.fund_agent  = FundamentalAgent()
        self.mkt_agent   = MarketAgent()
        self.fetcher     = DataFetcher()
        self.db          = Database()

    # ─── 每日全量掃描 ─────────────────────────────────────────

    def run_daily_scan(self, tickers: list) -> dict:
        logger.info(f"開始掃描 {len(tickers)} 隻候選股")

        market = self.mkt_agent.analyze()
        logger.info(f"大盤通行證: {market['gate']}")

        if market["gate"] == "red":
            return {
                "market":     market,
                "candidates": [],
                "skipped_reason": "大盤紅燈",
            }

        spy_df = self.fetcher.get_spy_ohlcv()

        candidates = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._analyze_and_cache, ticker, spy_df): ticker
                for ticker in tickers
            }
            for future, ticker in futures.items():
                try:
                    result = future.result(timeout=60)
                    if result:
                        candidates.append(result)
                except Exception as e:
                    logger.error(f"{ticker} 分析失敗: {e}")

        # 清理舊緩存
        self.db.cleanup_old_cache(keep_days=3)

        passed = [c for c in candidates if self._passes_threshold(c, market["gate"])]
        passed.sort(key=lambda x: x["composite_score"], reverse=True)

        if market["gate"] == "yellow":
            passed = [c for c in passed if c["composite_score"] >= 80]

        logger.info(f"通過門檻: {len(passed)} 隻")
        return {"market": market, "candidates": passed}

    # ─── 單股分析（優先讀緩存）────────────────────────────────

    def analyze_single(self, ticker: str) -> dict | None:
        """
        個股查詢：
        1. 先查 Supabase 緩存（最近3天）
        2. 有緩存 → 直接用，不需要重新抓數據
        3. 無緩存 → 即時抓數據分析
        """
        # 先查緩存
        cached = self.db.get_stock_cache(ticker, max_age_days=3)

        if cached and cached.get("tech_result") and cached.get("fund_result"):
            logger.info(f"{ticker}: 使用緩存數據（{cached['cache_date']}）")
            result = self._build_result_from_cache(ticker, cached)
            market = self.mkt_agent.analyze()
            if result:
                result["market"] = market
                result["from_cache"] = True
                result["cache_date"] = cached["cache_date"]
            return result

        # 無緩存，即時分析
        logger.info(f"{ticker}: 無緩存，即時分析")
        spy_df = self.fetcher.get_spy_ohlcv()
        result = self._analyze_one(ticker, spy_df, save_cache=True)
        market = self.mkt_agent.analyze()
        if result:
            result["market"] = market
            result["from_cache"] = False
        return result

    # ─── 分析單股並存緩存 ─────────────────────────────────────

    def _analyze_and_cache(self, ticker: str, spy_df) -> dict | None:
        """每日掃描用：分析完存入緩存"""
        return self._analyze_one(ticker, spy_df, save_cache=True)

    def _analyze_one(self, ticker: str, spy_df,
                     save_cache: bool = False) -> dict | None:
        try:
            df      = self.fetcher.get_ohlcv(ticker)
            info    = self.fetcher.get_info(ticker)
            fin     = self.fetcher.get_financials(ticker)

            if df is None:
                logger.warning(f"{ticker}: 無法取得數據")
                return None

            tech = self.tech_agent.analyze(ticker, df, spy_df)
            fund = self.fund_agent.analyze(ticker, info, fin)

            if fund.get("disqualified"):
                logger.info(f"{ticker} 排除: {fund['reason']}")
                return None

            if tech["details"]["atr_risk_pct"] > 8:
                logger.info(f"{ticker} 排除: ATR 風險過大")
                return None

            composite = self._calc_composite(tech["total"], fund["total"])

            result = {
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

            # 存緩存（本地掃描時）
            if save_cache:
                self.db.save_stock_cache(
                    ticker      = ticker,
                    ohlcv_df    = df,
                    fundamentals = fin,
                    tech_result  = tech,
                    fund_result  = fund,
                )

            return result

        except Exception as e:
            logger.error(f"{ticker} 分析異常: {e}")
            return None

    def _build_result_from_cache(self, ticker: str, cached: dict) -> dict | None:
        """從緩存數據重建 result dict"""
        try:
            tech = cached["tech_result"]
            fund = cached["fund_result"]

            if not tech or not fund:
                return None

            composite = self._calc_composite(
                tech.get("total", 0), fund.get("total", 0)
            )

            return {
                "ticker":          ticker,
                "short_name":      fund.get("ticker", ticker),
                "sector":          fund.get("details", {}).get("sector", "Unknown"),
                "price":           tech.get("details", {}).get("entry_price", 0),
                "tech_score":      tech.get("total", 0),
                "fund_score":      fund.get("total", 0),
                "composite_score": composite,
                "tech":            tech,
                "fund":            fund,
            }
        except Exception as e:
            logger.error(f"{ticker} 從緩存重建失敗: {e}")
            return None

    def _passes_threshold(self, result: dict, gate: str) -> bool:
        if result["tech_score"] < SCORE_THRESHOLD["technical"]:
            return False
        if result["fund_score"] < SCORE_THRESHOLD["fundamental"]:
            return False
        return True

    def _calc_composite(self, tech: float, fund: float) -> float:
        return round(tech * 0.55 + fund * 0.45)
