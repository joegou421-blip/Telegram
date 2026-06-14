import logging
from datetime import datetime
from screener.universe   import get_candidate_tickers
from agents.orchestrator import Orchestrator
from notifier.telegram   import TelegramNotifier
from watchlist.database  import Database
from config.settings     import TAKE_PROFIT_PCT
from web import dashboard_data

logger = logging.getLogger(__name__)


def run_full_scan(orchestrator: Orchestrator, notifier: TelegramNotifier, db: Database) -> dict:
    """每日全量掃描：抓候選股 → 分析 → 存歷史 → 觀察清單提醒 → 寫 Dashboard 快取"""
    tickers = get_candidate_tickers()
    logger.info(f"候選股數量: {len(tickers)}")

    scan_result = orchestrator.run_daily_scan(tickers)
    market      = scan_result["market"]
    candidates  = scan_result["candidates"]

    # 標記連續出現次數
    for c in candidates:
        c["consecutive_days"] = db.get_consecutive_appearances(c["ticker"], days=3)

    # 儲存掃描結果
    if candidates:
        db.save_scan_result(
            scan_date=datetime.now().strftime("%Y-%m-%d"),
            results=candidates,
            market=market,
        )

    # 檢查觀察清單
    _check_watchlist_alerts(db, orchestrator, notifier)

    # 寫入 Dashboard 快取
    dashboard_data.save_snapshot(scan_result)

    logger.info(f"=== 掃描完成，候選股 {len(candidates)} 隻 ===")
    return scan_result


def _check_watchlist_alerts(db: Database, orchestrator: Orchestrator,
                             notifier: TelegramNotifier):
    """檢查觀察清單：止盈提醒 + 形態惡化警告"""
    watchlist = db.get_watchlist()
    if not watchlist:
        return

    for item in watchlist:
        ticker = item["ticker"]
        try:
            result = orchestrator.analyze_single(ticker)
            if result is None:
                continue

            price_now    = result["price"]
            price_added  = item.get("price_added") or price_now
            gain_pct     = (price_now - price_added) / price_added
            old_tech     = item.get("tech_score", 70)
            new_tech     = result["tech_score"]
            breakout     = result["tech"]["details"].get("breakout_point")

            # 止盈提醒
            if gain_pct >= TAKE_PROFIT_PCT:
                notifier.notify_watchlist_alert(
                    ticker, "take_profit",
                    f"已上漲 {gain_pct*100:.1f}%（加入時 ${price_added:.2f} → 現在 ${price_now:.2f}）\n考慮部分獲利了結"
                )

            # 形態惡化警告
            elif new_tech < 60 and old_tech >= 70:
                notifier.notify_watchlist_alert(
                    ticker, "breakdown",
                    f"技術面評分從 {old_tech} 跌至 {new_tech}，形態可能惡化，考慮止損"
                )

            # 接近突破提醒
            elif breakout and price_now >= breakout * 0.98:
                notifier.notify_watchlist_alert(
                    ticker, "breakout",
                    f"股價 ${price_now:.2f} 接近突破點 ${breakout:.2f}，注意！"
                )

        except Exception as e:
            logger.error(f"觀察清單檢查失敗 {ticker}: {e}")
