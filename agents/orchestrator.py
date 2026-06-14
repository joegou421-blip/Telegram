import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from config.settings import (
    SCORE_THRESHOLD, EARNINGS_BUFFER_DAYS,
    PEAD_THRESHOLD, DASHBOARD_TOP_N_CLASSIC, DASHBOARD_TOP_N_MOMENTUM,
)
from agents.technical_agent   import TechnicalAgent
from agents.fundamental_agent import FundamentalAgent
from agents.market_agent      import MarketAgent
from data.fetcher              import DataFetcher
from data.indicators           import normalize_rs_ratings, calc_sector_stats, calc_sector_score
from watchlist.database        import Database
from web                        import dashboard_data

logger = logging.getLogger(__name__)

# 大盤通行證 → 倉位計算用的風險等級
MARKET_RISK_MAP = {"green": "low", "yellow": "medium", "red": "high"}


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
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(self._analyze_and_cache, ticker, spy_df,
                                 market["gate"], market.get("regime", "normal")): ticker
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

        # RS Rating 第二階段：全市場百分位排名，並重算受影響的分數
        normalize_rs_ratings(candidates)
        for c in candidates:
            self._recalc_rs_score(c)

        # 板塊統計 + 個股板塊強度分
        sector_stats = calc_sector_stats(candidates)
        self._attach_sector_ranks(candidates, sector_stats)
        for c in candidates:
            sector    = c.get("sector", "Unknown") or "Unknown"
            rs_rating = c.get("tech", {}).get("details", {}).get("rs_rating", 0) or 0
            c["sector_score"] = calc_sector_score(c["ticker"], sector, rs_rating, sector_stats)
            # 重算分類（這次有 sector_score，Leadership 更準確）
            self._attach_classification(c, market["gate"])

        passed = [c for c in candidates if self._passes_threshold(c, market["gate"])]
        passed.sort(key=lambda x: x["composite_score"], reverse=True)

        if market["gate"] == "yellow":
            passed = [c for c in passed if c["composite_score"] >= 80]

        logger.info(f"通過門檻: {len(passed)} 隻")

        # Classic Setups Top N（依 Classic Ranking 排序）
        classic_setups = [c for c in candidates if c["classification"] == "classic"]
        classic_setups.sort(key=lambda x: x["classic_ranking"], reverse=True)
        classic_setups = classic_setups[:DASHBOARD_TOP_N_CLASSIC]

        # Momentum Monsters Top N（獨立區塊，依 Momentum Ranking 排序）
        momentum_monsters = [c for c in candidates if c["pead_score"] >= PEAD_THRESHOLD]
        momentum_monsters.sort(key=lambda x: x["momentum_ranking"], reverse=True)
        momentum_monsters = momentum_monsters[:DASHBOARD_TOP_N_MOMENTUM]

        return {
            "market":            market,
            "candidates":        passed,
            "sector_stats":       sector_stats,
            "classic_setups":     classic_setups,
            "momentum_monsters":  momentum_monsters,
        }

    # ─── 單股分析（優先讀緩存）────────────────────────────────

    def analyze_single(self, ticker: str) -> dict | None:
        """
        個股查詢：
        1. 先查 Supabase 緩存（最近3天）
        2. 有緩存 → 直接用，不需要重新抓數據
        3. 無緩存 → 即時抓數據分析
        """
        market = self.mkt_agent.analyze()

        # 先查緩存
        cached = self.db.get_stock_cache(ticker, max_age_days=3)

        if cached and cached.get("tech_result") and cached.get("fund_result"):
            logger.info(f"{ticker}: 使用緩存數據（{cached['cache_date']}）")
            result = self._build_result_from_cache(ticker, cached)
            if result:
                result["market"] = market
                result["from_cache"] = True
                result["cache_date"] = cached["cache_date"]
                self._attach_earnings_info(result, ticker)
                self._attach_trade_plan(result, market["gate"])
                self._attach_sector_score(result)
                self._attach_classification(result, market["gate"])
            return result

        # 無緩存，即時分析
        logger.info(f"{ticker}: 無緩存，即時分析")
        spy_df = self.fetcher.get_spy_ohlcv()
        result = self._analyze_one(ticker, spy_df, save_cache=True,
                                    market_gate=market["gate"], market_regime=market.get("regime", "normal"))
        if result:
            result["market"] = market
            result["from_cache"] = False
        return result

    # ─── 分析單股並存緩存 ─────────────────────────────────────

    def _analyze_and_cache(self, ticker: str, spy_df, market_gate: str = "green",
                           market_regime: str = "normal") -> dict | None:
        """每日掃描用：分析完存入緩存"""
        return self._analyze_one(ticker, spy_df, save_cache=True,
                                  market_gate=market_gate, market_regime=market_regime)

    def _analyze_one(self, ticker: str, spy_df, save_cache: bool = False,
                     market_gate: str = "green", market_regime: str = "normal") -> dict | None:
        try:
            df      = self.fetcher.get_ohlcv(ticker)
            info    = self.fetcher.get_info(ticker)
            fin     = self.fetcher.get_financials(ticker)

            if df is None:
                logger.warning(f"{ticker}: 無法取得數據")
                return None

            tech = self.tech_agent.analyze(ticker, df, spy_df, regime=market_regime)
            fund = self.fund_agent.analyze(ticker, info, fin)

            if fund.get("disqualified"):
                logger.info(f"{ticker} 排除: {fund['reason']}")
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

            # 財報倒數警示（用 get_info 已抓到的 earnings_date，避免重複呼叫）+ 倉位/持倉管理建議
            self._attach_earnings_info(result, ticker, earnings_date=info.get("earnings_date"))
            self._attach_trade_plan(result, market_gate)
            self._attach_sector_score(result)
            self._attach_classification(result, market_gate)

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

    def _attach_earnings_info(self, result: dict, ticker: str, earnings_date=None) -> None:
        """檢查財報日期，落在 EARNINGS_BUFFER_DAYS 內就在 tags 加警示"""
        if earnings_date is None:
            earnings_date = self.fetcher.get_next_earnings(ticker)
        earnings_soon = False
        if earnings_date is not None:
            days_to_earnings = (earnings_date.date() - datetime.now().date()).days
            if 0 <= days_to_earnings <= EARNINGS_BUFFER_DAYS:
                earnings_soon = True
                result["tech"]["tags"].append((f"財報倒數 {days_to_earnings} 天，注意波動", "warn"))

        result["earnings_date"] = str(earnings_date.date()) if earnings_date is not None else None
        result["earnings_soon"] = earnings_soon

    def _attach_trade_plan(self, result: dict, market_gate: str) -> None:
        """根據大盤風險、個股分級計算倉位大小與持倉管理計劃"""
        tech    = result.get("tech", {})
        details = tech.get("details", {})
        grade   = tech.get("grade", "C")
        deviation_pct = tech.get("deviation_pct", 0)
        market_risk   = MARKET_RISK_MAP.get(market_gate, "medium")

        result["position_size"]    = self.tech_agent.calc_position_size(grade, market_risk, deviation_pct)
        result["trade_management"] = self.tech_agent.calc_trade_management(
            details.get("entry_price", 0), details.get("stop_loss", 0)
        )

    def _attach_classification(self, result: dict, market_gate: str) -> None:
        """補上 Leadership / Timing / Ranking / Why / PEAD 分類（Pre-Market Decision Assistant）"""
        tech, fund = result["tech"], result["fund"]
        sector_score = result.get("sector_score")  # 單股查詢時為 None

        leadership = self.tech_agent.calc_leadership_score(tech, fund, sector_score)
        timing     = self.tech_agent.calc_timing_score(tech)
        pead       = tech["details"].get("pead") or {"score": 0, "detected": False, "days_since": None,
                                                        "gap_pct": 0, "vol_ratio": 0,
                                                        "earnings_high": None, "reaction_date": None}

        classic_ranking  = self.tech_agent.calc_classic_ranking(leadership["score"], timing["score"])
        momentum_ranking = self.tech_agent.calc_momentum_ranking(leadership["score"], pead["score"])

        is_classic  = self._passes_threshold(result, market_gate) and not fund.get("disqualified")
        is_momentum = pead["score"] >= PEAD_THRESHOLD
        classification = "classic" if is_classic else ("momentum" if is_momentum else "none")

        result["leadership"]       = leadership
        result["timing"]           = timing
        result["classic_ranking"]  = round(classic_ranking, 1)
        result["momentum_ranking"] = round(momentum_ranking, 1)
        result["pead_score"]       = pead["score"]
        result["classification"]   = classification
        result["why_classic"]      = self.tech_agent.build_classic_why(tech, leadership, timing)
        result["why_momentum"]     = self.tech_agent.build_momentum_why(tech, pead)
        result["not_recommended_reasons"] = (
            [] if classification != "none"
            else self.tech_agent.build_not_recommended_reasons(tech, fund, leadership, timing)
        )
        result["pivot"] = tech["details"].get("breakout_point")
        result["stop"]  = tech["details"].get("stop_loss")

    def _recalc_rs_score(self, result: dict) -> None:
        """RS Rating 經 normalize_rs_ratings 重算百分位後，同步更新 breakdown/total/composite"""
        tech    = result.get("tech", {})
        details = tech.get("details", {})
        breakdown = tech.get("breakdown")
        if not breakdown:
            return

        new_rating   = details.get("rs_rating", 50)
        new_rs_score = self.tech_agent.rs_rating_to_score(new_rating)
        old_rs_score = breakdown.get("rs_rating", new_rs_score)

        breakdown["rs_rating"] = new_rs_score
        tech["total"] = round(tech.get("total", 0) - old_rs_score + new_rs_score)

        # 同步更新 tags 裡的 RS Rating 標籤（原本是用全市場百分位前的原始值）
        tags = tech.get("tags", [])
        for i, (label, t_type) in enumerate(tags):
            if label.startswith("RS Rating"):
                tags[i] = self.tech_agent.rs_rating_tag(new_rating)
                break

        result["tech_score"]      = tech["total"]
        result["composite_score"] = self._calc_composite(tech["total"], result.get("fund_score", 0))

    def _attach_sector_score(self, result: dict) -> None:
        """單股查詢：用最近一次全市場掃描留下的板塊統計快取，算這隻股票的 sector_score"""
        sector_stats = dashboard_data.load_sector_stats()
        if not sector_stats:
            return
        sector    = result.get("sector", "Unknown") or "Unknown"
        rs_rating = result.get("tech", {}).get("details", {}).get("rs_rating", 0) or 0
        result["sector_score"] = calc_sector_score(result["ticker"], sector, rs_rating, sector_stats)

    def _attach_sector_ranks(self, candidates: list, sector_stats: dict) -> None:
        """補上每個板塊內，依 RS Rating 排序的個股清單，供 calc_sector_score 算個股排名用"""
        by_sector = {}
        for c in candidates:
            sector    = c.get("sector", "Unknown") or "Unknown"
            rs_rating = c.get("tech", {}).get("details", {}).get("rs_rating", 0) or 0
            by_sector.setdefault(sector, []).append((c["ticker"], rs_rating))

        for sector, pairs in by_sector.items():
            pairs.sort(key=lambda x: x[1], reverse=True)
            if sector in sector_stats:
                sector_stats[sector]["tickers_with_rs"] = pairs

    def _passes_threshold(self, result: dict, gate: str) -> bool:
        if result["tech_score"] < SCORE_THRESHOLD["technical"]:
            return False
        if result["fund_score"] < SCORE_THRESHOLD["fundamental"]:
            return False
        return True

    def _calc_composite(self, tech: float, fund: float) -> float:
        return round(tech * 0.55 + fund * 0.45)
