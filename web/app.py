import logging
from flask import Flask, render_template, redirect, url_for, request, jsonify

from agents.orchestrator import Orchestrator
from notifier.telegram   import TelegramNotifier
from watchlist.database  import Database
import scan_runner
from web import dashboard_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

orchestrator = Orchestrator()
notifier     = TelegramNotifier()
db           = Database()


@app.route("/")
def dashboard():
    snapshot = dashboard_data.load_snapshot()
    return render_template("dashboard.html", snapshot=snapshot)


@app.route("/scan", methods=["POST"])
def run_scan():
    try:
        scan_runner.run_full_scan(orchestrator, notifier, db)
    except Exception as e:
        logger.error(f"掃描失敗: {e}")
    return redirect(url_for("dashboard"))


@app.route("/stock/<ticker>")
def stock_detail(ticker):
    ticker = ticker.upper().strip()
    result = orchestrator.analyze_single(ticker)
    if result is None:
        return render_template("stock.html", ticker=ticker, result=None)
    watchlist_entry = db.is_in_watchlist(ticker)
    return render_template("stock.html", ticker=ticker, result=result,
                            watchlist_entry=watchlist_entry)


@app.route("/watchlist")
def watchlist():
    items = db.get_watchlist()
    return render_template("watchlist.html", items=items)


@app.route("/watchlist/add/<ticker>", methods=["POST"])
def watchlist_add(ticker):
    ticker = ticker.upper().strip()
    result = orchestrator.analyze_single(ticker)
    if result is not None:
        db.add_to_watchlist(ticker, result)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/watchlist/remove/<ticker>", methods=["POST"])
def watchlist_remove(ticker):
    ticker = ticker.upper().strip()
    db.remove_from_watchlist(ticker)
    return redirect(request.referrer or url_for("watchlist"))


@app.route("/api/premarket/<ticker>")
def api_premarket(ticker):
    ticker = ticker.upper().strip()
    quote = orchestrator.fetcher.get_premarket_quote(ticker)
    if quote is None:
        return jsonify({"ticker": ticker, "available": False})

    alert = None
    change_pct = quote["change_pct"]

    pivot = request.args.get("pivot", type=float)
    stop  = request.args.get("stop", type=float)

    if pivot:
        dist_to_pivot = round((quote["last_price"] - pivot) / pivot * 100, 1)
        quote["dist_to_pivot_pct"] = dist_to_pivot

    if stop and quote["last_price"] <= stop:
        alert = f"⚠️ 跌破止損價 ${stop:.2f}"
    elif change_pct <= -4.5:
        alert = f"⚠️ Pre-market {change_pct:+.1f}%，跌破理想進場區間"
    elif change_pct >= 3:
        alert = f"Pre-market {change_pct:+.1f}%，留意是否追高"

    return jsonify({
        "ticker": ticker,
        "available": True,
        **quote,
        "alert": alert,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
