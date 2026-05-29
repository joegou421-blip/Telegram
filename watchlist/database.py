import logging
import json
import pandas as pd
from datetime import datetime, date, timedelta
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
                "ticker":       ticker,
                "short_name":   result.get("short_name", ticker),
                "sector":       result.get("sector", "Unknown"),
                "price_added":  result.get("price"),
                "tech_score":   result.get("tech_score"),
                "fund_score":   result.get("fund_score"),
                "composite":    result.get("composite_score"),
                "stop_loss":    result.get("tech", {}).get("details", {}).get("stop_loss"),
                "added_at":     datetime.now().isoformat(),
                "status":       "watching",
            }).execute()
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

    # ─── 股票數據緩存 ─────────────────────────────────────────

    def save_stock_cache(self, ticker: str, ohlcv_df: pd.DataFrame,
                         fundamentals: dict, tech_result: dict,
                         fund_result: dict) -> bool:
        """
        把本地掃描的數據存入 Supabase 緩存
        之後在外面查個股可以直接讀，不需要重新抓數據
        """
        try:
            today = date.today().isoformat()

            # DataFrame 轉 JSON（只存最近 400 天，夠計算所有技術指標）
            ohlcv_json = None
            if ohlcv_df is not None and not ohlcv_df.empty:
                df_recent = ohlcv_df.tail(400).copy()
                df_recent.index = df_recent.index.strftime("%Y-%m-%d")
                ohlcv_json = df_recent.to_dict(orient="index")

            self.client.table("stock_data_cache").upsert({
                "ticker":       ticker,
                "cache_date":   today,
                "ohlcv_json":   ohlcv_json,
                "fundamentals": fundamentals,
                "tech_result":  self._serialize(tech_result),
                "fund_result":  self._serialize(fund_result),
                "updated_at":   datetime.now().isoformat(),
            }, on_conflict="ticker,cache_date").execute()

            logger.info(f"{ticker}: 數據已緩存到 Supabase")
            return True

        except Exception as e:
            logger.error(f"{ticker} 緩存失敗: {e}")
            return False

    def get_stock_cache(self, ticker: str, max_age_days: int = 3) -> dict | None:
        """
        讀取緩存數據
        max_age_days: 最多接受幾天前的數據（預設3天，覆蓋週末）
        """
        try:
            cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()

            res = (
                self.client.table("stock_data_cache")
                .select("*")
                .eq("ticker", ticker)
                .gte("cache_date", cutoff)
                .order("cache_date", desc=True)
                .limit(1)
                .execute()
            )

            if not res.data:
                logger.info(f"{ticker}: 無緩存或緩存過期")
                return None

            row = res.data[0]
            logger.info(f"{ticker}: 從緩存讀取（{row['cache_date']}）")

            # 還原 OHLCV DataFrame
            ohlcv_df = None
            if row.get("ohlcv_json"):
                ohlcv_df = pd.DataFrame.from_dict(
                    row["ohlcv_json"], orient="index"
                )
                ohlcv_df.index = pd.to_datetime(ohlcv_df.index)
                ohlcv_df = ohlcv_df.sort_index()

            return {
                "ticker":       ticker,
                "cache_date":   row["cache_date"],
                "ohlcv_df":     ohlcv_df,
                "fundamentals": row.get("fundamentals") or {},
                "tech_result":  row.get("tech_result") or {},
                "fund_result":  row.get("fund_result") or {},
            }

        except Exception as e:
            logger.error(f"{ticker} 讀取緩存失敗: {e}")
            return None

    def get_all_cached_tickers(self, max_age_days: int = 3) -> list:
        """取得所有有效緩存的股票代碼"""
        try:
            cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
            res = (
                self.client.table("stock_data_cache")
                .select("ticker, cache_date")
                .gte("cache_date", cutoff)
                .order("cache_date", desc=True)
                .execute()
            )
            seen = set()
            tickers = []
            for row in (res.data or []):
                if row["ticker"] not in seen:
                    seen.add(row["ticker"])
                    tickers.append(row["ticker"])
            return tickers
        except Exception as e:
            logger.error(f"取得緩存清單失敗: {e}")
            return []

    def cleanup_old_cache(self, keep_days: int = 3):
        """清理超過 keep_days 天的舊緩存"""
        try:
            cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
            self.client.table("stock_data_cache").delete().lt(
                "cache_date", cutoff
            ).execute()
            logger.info(f"已清理 {cutoff} 之前的緩存")
        except Exception as e:
            logger.error(f"清理緩存失敗: {e}")

    # ─── 輔助方法 ─────────────────────────────────────────────

    def _serialize(self, obj):
        """把 result dict 序列化成可存入 JSON 的格式"""
        if obj is None:
            return None
        try:
            return json.loads(json.dumps(obj, default=str))
        except Exception:
            return {}
