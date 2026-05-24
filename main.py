import logging
import sys
from datetime import datetime
from screener.universe  import get_candidate_tickers
from agents.orchestrator import Orchestrator
from notifier.telegram   import TelegramNotifier
from watchlist.database  import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== Swing Trade Agent 開始 ===")

    orchestrator = Orchestrator()
    notifier     = TelegramNotifier()
    db           = Database()

    # 1. 取得候選股清單
    tickers = get_candidate_tickers()
    logger.info(f"候選股數量: {len(tickers)}")

    # 2. 跑分析
    scan_result = orchestrator.run_daily_scan(tickers)
    market      = scan_result["market"]
    candidates  = scan_result["candidates"]

    # 3. 儲存到 Supabase
    if candidates:
        db.save_scan_result(
            scan_date=datetime.now().strftime("%Y-%m-%d"),
            results=candidates,
            market=market,
        )

    # 4. 檢查觀察清單變化
    _check_watchlist_alerts(db, orchestrator, notifier)

    # 5. 推送 Telegram
    notifier.send_daily_report(scan_result)

    logger.info(f"=== 完成，推送 {len(candidates)} 隻候選股 ===")


def _check_watchlist_alerts(db: Database, orchestrator: Orchestrator, notifier: TelegramNotifier):
    """每次掃描順便檢查觀察清單"""
    watchlist = db.get_watchlist()
    if not watchlist:
        return

    for item in watchlist:
        ticker = item["ticker"]
        try:
            result = orchestrator.analyze_single(ticker)
            if result is None:
                continue

            old_tech = item.get("tech_score", 70)
            new_tech = result["tech_score"]

            # 形態惡化通知
            if new_tech < 60 and old_tech >= 70:
                notifier.notify_watchlist_alert(
                    ticker,
                    "breakdown",
                    f"技術面評分從 {old_tech} 跌至 {new_tech}，形態可能已惡化"
                )

            # 接近突破通知
            breakout = result["tech"]["details"].get("breakout_point")
            price    = result["price"]
            if breakout and price >= breakout * 0.98:
                notifier.notify_watchlist_alert(
                    ticker,
                    "breakout",
                    f"股價 ${price} 接近突破點 ${breakout}，注意！"
                )

        except Exception as e:
            logger.error(f"觀察清單檢查失敗 {ticker}: {e}")


if __name__ == "__main__":
    main()
