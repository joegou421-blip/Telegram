import logging
import sys
from datetime import datetime
import scan_runner
from agents.orchestrator import Orchestrator
from notifier.telegram   import TelegramNotifier
from watchlist.database  import Database
from config.settings     import SCAN_TICKER

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

    # 單股掃描模式（Telegram 觸發時）
    if SCAN_TICKER:
        logger.info(f"單股分析模式：{SCAN_TICKER}")
        _run_single_stock(SCAN_TICKER, orchestrator, notifier, db)
        return

    # 每日全量掃描模式
    _run_daily_scan(orchestrator, notifier, db)


def _run_single_stock(ticker: str, orchestrator: Orchestrator,
                      notifier: TelegramNotifier, db: Database):
    """單股分析：Telegram 觸發時使用"""
    result = orchestrator.analyze_single(ticker)
    if result is None:
        notifier.send_error_message(f"無法分析 {ticker}，請確認股票代碼是否正確。")
        return

    # 儲存到 scan_history
    market = result.get("market", {})
    db.save_scan_result(
        scan_date=datetime.now().strftime("%Y-%m-%d"),
        results=[result],
        market=market,
    )

    # 查觀察清單狀態
    watchlist_entry = db.is_in_watchlist(ticker)

    # 推送完整評分卡
    notifier.send_stock_card(result, watchlist_entry)
    logger.info(f"{ticker} 分析完成，技術面 {result['tech_score']} 基本面 {result['fund_score']}")


def _run_daily_scan(orchestrator: Orchestrator,
                    notifier: TelegramNotifier, db: Database):
    """每日全量掃描"""
    scan_result = scan_runner.run_full_scan(orchestrator, notifier, db)
    logger.info(f"=== 完成，候選股 {len(scan_result['candidates'])} 隻 ===")


if __name__ == "__main__":
    main()
