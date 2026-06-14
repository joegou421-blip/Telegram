import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SNAPSHOT_PATH     = os.path.join(os.path.dirname(__file__), "dashboard_snapshot.json")
SECTOR_STATS_PATH = os.path.join(os.path.dirname(__file__), "sector_stats_cache.json")


def save_snapshot(scan_result: dict) -> None:
    """把掃描結果精簡後寫入本地 JSON，供 Dashboard 讀取"""
    market = scan_result.get("market", {})

    snapshot = {
        "scan_time": datetime.now().isoformat(),
        "market": {
            "gate":             market.get("gate"),
            "regime":           market.get("regime"),
            "distribution_days": market.get("distribution_days"),
            "summary":          market.get("summary"),
        },
        "classic_setups": [
            _summarize_classic(c) for c in scan_result.get("classic_setups", [])
        ],
        "momentum_monsters": [
            _summarize_momentum(c) for c in scan_result.get("momentum_monsters", [])
        ],
    }

    try:
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"寫入 Dashboard 快取失敗: {e}")


def load_snapshot() -> dict | None:
    if not os.path.exists(SNAPSHOT_PATH):
        return None
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"讀取 Dashboard 快取失敗: {e}")
        return None


def save_sector_stats(sector_stats: dict) -> None:
    """把全市場掃描算出的板塊統計寫入本地 JSON，供單股查詢算 sector_score 用"""
    try:
        with open(SECTOR_STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(sector_stats, f, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"寫入板塊統計快取失敗: {e}")


def load_sector_stats() -> dict | None:
    if not os.path.exists(SECTOR_STATS_PATH):
        return None
    try:
        with open(SECTOR_STATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"讀取板塊統計快取失敗: {e}")
        return None


def _summarize_classic(c: dict) -> dict:
    return {
        "ticker":         c["ticker"],
        "short_name":     c.get("short_name", c["ticker"]),
        "price":          c.get("price"),
        "leadership":     c.get("leadership", {}).get("score"),
        "timing":         c.get("timing", {}).get("score"),
        "classic_ranking": c.get("classic_ranking"),
        "why":            c.get("why_classic", []),
        "pivot":          c.get("pivot"),
        "stop":           c.get("stop"),
    }


def _summarize_momentum(c: dict) -> dict:
    return {
        "ticker":          c["ticker"],
        "short_name":      c.get("short_name", c["ticker"]),
        "price":           c.get("price"),
        "leadership":      c.get("leadership", {}).get("score"),
        "pead_score":      c.get("pead_score"),
        "momentum_ranking": c.get("momentum_ranking"),
        "why":             c.get("why_momentum", []),
    }
