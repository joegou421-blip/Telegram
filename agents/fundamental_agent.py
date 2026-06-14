import numpy as np
import logging
from datetime import datetime, timedelta
from config.settings import FUND_WEIGHTS, EARNINGS_BUFFER_DAYS

logger = logging.getLogger(__name__)


class FundamentalAgent:

    def analyze(self, ticker: str, info: dict, financials: dict) -> dict:
        """完整基本面分析"""

        # 硬性過濾：Earnings 太近
        earnings_date = info.get("earnings_date")
        earnings_warning = None
        if earnings_date:
            days_to_earnings = (earnings_date - datetime.now()).days
            if 0 <= days_to_earnings <= EARNINGS_BUFFER_DAYS:
                return {
                    "ticker":          ticker,
                    "total":           0,
                    "disqualified":    True,
                    "reason":          f"Earnings 在 {days_to_earnings} 天後，跳過",
                    "earnings_date":   str(earnings_date.date()),
                }
            elif 0 <= days_to_earnings <= 14:
                earnings_warning = f"注意：Earnings 在 {days_to_earnings} 天後"

        eps_score  = self._score_eps(financials.get("eps_quarters", []))
        rev_score  = self._score_revenue(financials)
        inst_score = self._score_institutional(financials)
        sec_score  = self._score_sector(info)

        total = eps_score + rev_score + inst_score + sec_score

        return {
            "ticker":   ticker,
            "total":    round(total),
            "disqualified": False,
            "eps_quarters": financials.get("eps_quarters", []),
            "breakdown": {
                "eps_acceleration": eps_score,
                "revenue_margin":   rev_score,
                "institutional":    inst_score,
                "sector_strength":  sec_score,
            },
            "details": {
                "sector":            info.get("sector", "Unknown"),
                "industry":          info.get("industry", "Unknown"),
                "gross_margin":      round(financials.get("gross_margin", 0) * 100, 1),
                "institutional_pct": round(financials.get("institutional_pct", 0) * 100, 1),
                "eps_growth_rate":   self._calc_eps_growth(financials.get("eps_quarters", [])),
                "earnings_date":     str(earnings_date.date()) if earnings_date else "N/A",
                "earnings_warning":  earnings_warning,
            },
            "tags": self._build_tags(eps_score, rev_score, inst_score, sec_score, earnings_warning),
        }

    # ─── EPS 加速增長評分（滿分 30）──────────────────────────

    def _score_eps(self, eps_quarters: list) -> float:
        if len(eps_quarters) < 3:
            return 10  # 數據不足給中等

        score = 0
        eps = [abs(e) for e in eps_quarters[:4]]  # 最近4季，由新到舊

        # 最新季是否正數盈利
        if eps_quarters[0] > 0:
            score += 8

        # 計算 YoY 增長（需要4季數據）
        if len(eps) >= 4:
            growth_recent = (eps[0] - eps[2]) / abs(eps[2]) if eps[2] != 0 else 0
            growth_prev   = (eps[1] - eps[3]) / abs(eps[3]) if eps[3] != 0 else 0

            # 增長率本身
            if growth_recent > 0.40:
                score += 12
            elif growth_recent > 0.20:
                score += 8
            elif growth_recent > 0.10:
                score += 4

            # 加速度（最新增長率 > 上一季增長率）
            if growth_recent > growth_prev:
                score += 10  # 加速中！

        return min(score, FUND_WEIGHTS["eps_acceleration"])

    # ─── 營收 + 毛利率評分（滿分 25）──────────────────────────

    def _score_revenue(self, financials: dict) -> float:
        score = 0
        rev   = financials.get("revenue_quarters", [])
        gm    = financials.get("gross_margin", 0)

        # 毛利率
        if gm > 0.60:
            score += 10
        elif gm > 0.40:
            score += 7
        elif gm > 0.20:
            score += 4

        # 營收增長
        if len(rev) >= 4:
            growth = (rev[0] - rev[2]) / abs(rev[2]) if rev[2] != 0 else 0
            if growth > 0.25:
                score += 15
            elif growth > 0.15:
                score += 10
            elif growth > 0.05:
                score += 5

        return min(score, FUND_WEIGHTS["revenue_margin"])

    # ─── 機構持倉評分（滿分 25）───────────────────────────────

    def _score_institutional(self, financials: dict) -> float:
        score = 0
        inst_pct = financials.get("institutional_pct", 0)

        # 機構持倉比例
        if inst_pct > 0.70:
            score += 15
        elif inst_pct > 0.50:
            score += 10
        elif inst_pct > 0.30:
            score += 5

        # 機構持倉趨勢（這裡用 heldPercentInstitutions 近似）
        inst_change = financials.get("institutional_change", 0)
        if inst_change > inst_pct:
            score += 10  # 持倉增加
        else:
            score += 5

        return min(score, FUND_WEIGHTS["institutional"])

    # ─── Sector 強弱評分（滿分 20）───────────────────────────

    def _score_sector(self, info: dict) -> float:
        # 強勢 Sector 列表（根據當前市場環境可調整）
        strong_sectors = {
            "Technology":           20,
            "Communication Services": 18,
            "Consumer Discretionary": 16,
            "Industrials":          14,
            "Health Care":          14,
            "Financial Services":   12,
            "Energy":               12,
            "Materials":            10,
            "Real Estate":          8,
            "Consumer Staples":     8,
            "Utilities":            6,
        }
        sector = info.get("sector", "")
        return strong_sectors.get(sector, 10)

    # ─── 輔助方法 ────────────────────────────────────────────

    def _calc_eps_growth(self, eps_quarters: list) -> str:
        if len(eps_quarters) < 3:
            return "N/A"
        eps = eps_quarters
        if eps[2] != 0:
            growth = (eps[0] - eps[2]) / abs(eps[2]) * 100
            return f"{growth:+.1f}%"
        return "N/A"

    def _build_tags(self, eps_score, rev_score, inst_score, sec_score, earnings_warning) -> list:
        tags = []
        if eps_score >= 25:
            tags.append(("EPS 強勁加速", "ok"))
        elif eps_score >= 15:
            tags.append(("EPS 穩定增長", "ok"))
        else:
            tags.append(("EPS 增長偏弱", "warn"))

        if inst_score >= 20:
            tags.append(("機構持倉強", "ok"))

        if sec_score >= 16:
            tags.append(("強勢 Sector", "ok"))
        elif sec_score <= 8:
            tags.append(("Sector 偏弱", "warn"))

        if earnings_warning:
            tags.append((earnings_warning, "warn"))

        return tags
