import logging
from datetime import datetime
from supabase import create_client
from config.settings import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)


class Database:

    def __init__(self):
        self.client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # ─── 觀察清單 ─────────────────────────────────────────────

    def get_watchlist(self) -> list:
        try:
            res = self.client.table("watchlist").select("*").eq("status", "watching").execute()
            return res.data or []
        except Exception as e:
            logger.error(f"取得觀察清單失敗: {e}")
            return []

    def add_to_watchlist(self, ticker: str, result: dict) -> bool:
        try:
            self.client.table("watchlist").upsert({
                "ticker":        ticker,
                "short_name":    result.get("short_name", ticker),
                "sector":        result.get("sector", "Unknown"),
                "price_added":   result.get("price"),
                "tech_score":    result.get("tech_score"),
                "fund_score":    result.get("fund_score"),
                "composite":     result.get("composite_score"),
                "stop_loss":     result.get("tech", {}).get("details", {}).get("stop_loss"),
                "added_at":      datetime.now().isoformat(),
                "status":        "watching",
            }).execute()
            logger.info(f"{ticker} 已加入觀察清單")
            return True
        except Exception as e:
            logger.error(f"加入觀察清單失敗: {e}")
            return False

    def remove_from_watchlist(self, ticker: str) -> bool:
        try:
            self.client.table("watchlist").update({
                "status":     "removed",
                "removed_at": datetime.now().isoformat(),
            }).eq("ticker", ticker).execute()
            logger.info(f"{ticker} 已從觀察清單移除")
            return True
        except Exception as e:
            logger.error(f"移除觀察清單失敗: {e}")
            return False

    def is_in_watchlist(self, ticker: str) -> dict | None:
        try:
            res = (
                self.client.table("watchlist")
                .select("*")
                .eq("ticker", ticker)
                .eq("status", "watching")
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"查詢觀察清單失敗: {e}")
            return None

    # ─── 掃描歷史 ─────────────────────────────────────────────

    def save_scan_result(self, scan_date: str, results: list, market: dict):
        try:
            for r in results:
                self.client.table("scan_history").insert({
                    "scan_date":       scan_date,
                    "ticker":          r["ticker"],
                    "tech_score":      r["tech_score"],
                    "fund_score":      r["fund_score"],
                    "composite":       r["composite_score"],
                    "price":           r["price"],
                    "market_gate":     market["gate"],
                    "pattern_type":    r["tech"]["details"].get("pattern_type"),
                    "stop_loss":       r["tech"]["details"].get("stop_loss"),
                    "breakout_point":  r["tech"]["details"].get("breakout_point"),
                }).execute()
        except Exception as e:
            logger.error(f"儲存掃描結果失敗: {e}")

    def get_consecutive_appearances(self, ticker: str, days: int = 3) -> int:
        """計算某股票連續出現在候選清單的天數"""
        try:
            res = (
                self.client.table("scan_history")
                .select("scan_date")
                .eq("ticker", ticker)
                .order("scan_date", desc=True)
                .limit(days)
                .execute()
            )
            return len(res.data) if res.data else 0
        except Exception as e:
            logger.error(f"查詢出現次數失敗: {e}")
            return 0
