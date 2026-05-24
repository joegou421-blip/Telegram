import requests
import logging
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

GATE_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
TAG_EMOJI  = {"ok": "✅", "warn": "⚠️", "bad": "❌"}


class TelegramNotifier:

    def send_daily_report(self, scan_result: dict):
        """發送每日掃描報告"""
        market     = scan_result["market"]
        candidates = scan_result["candidates"]

        # 大盤摘要
        header = self._format_market_header(market)
        self._send_message(header)

        if not candidates:
            self._send_message("今日無符合條件的候選股。")
            return

        # 每隻股票單獨發一條，帶按鈕
        for stock in candidates[:15]:
            msg, keyboard = self._format_stock_card(stock, market, in_watchlist=None)
            self._send_message(msg, keyboard)

    def send_stock_card(self, result: dict, in_watchlist_entry: dict | None):
        """發送單一股票分析卡（你主動問的時候用）"""
        market   = result.get("market", {})
        msg, kbd = self._format_stock_card(result, market, in_watchlist_entry)
        self._send_message(msg, kbd)

    def send_watchlist_summary(self, watchlist: list, updated_results: list):
        """發送觀察清單總結"""
        if not watchlist:
            self._send_message("你的觀察清單目前是空的。")
            return

        lines = [f"👁 觀察清單（{len(watchlist)} 隻）\n"]
        for item in updated_results:
            ticker = item["ticker"]
            old_score = next(
                (w["composite"] for w in watchlist if w["ticker"] == ticker), "N/A"
            )
            status_icon = "✅" if item["composite_score"] >= 70 else "⚠️"
            lines.append(
                f"{status_icon} {ticker}  "
                f"技術 {item['tech_score']} · 基本面 {item['fund_score']}\n"
                f"  {item['tech']['details'].get('pattern_notes', '')}"
            )

        self._send_message("\n".join(lines))

    def notify_watchlist_alert(self, ticker: str, alert_type: str, detail: str):
        """主動通知觀察清單變化"""
        icon = "🚨" if alert_type == "breakdown" else "🎯"
        self._send_message(f"{icon} 觀察清單提醒：{ticker}\n{detail}")

    # ─── 格式化 ──────────────────────────────────────────────

    def _format_market_header(self, market: dict) -> str:
        gate    = market["gate"]
        emoji   = GATE_EMOJI.get(gate, "⚪")
        summary = market.get("summary", "")
        advice  = {
            "green":  "正常倉位進場",
            "yellow": "建議倉位減半",
            "red":    "暫停做多，等待觀望",
        }.get(gate, "")
        return f"{emoji} 大盤狀態：{summary}\n建議：{advice}"

    def _format_stock_card(self, result: dict, market: dict, in_watchlist_entry) -> tuple:
        ticker       = result["ticker"]
        short_name   = result.get("short_name", ticker)
        price        = result["price"]
        tech         = result["tech"]
        fund         = result["fund"]
        composite    = result["composite_score"]
        tech_score   = result["tech_score"]
        fund_score   = result["fund_score"]
        stop_loss    = tech["details"].get("stop_loss", 0)
        atr_risk     = tech["details"].get("atr_risk_pct", 0)
        breakout     = tech["details"].get("breakout_point")
        pattern_type = tech["details"].get("pattern_type", "")
        pattern_note = tech["details"].get("pattern_notes", "")
        gate         = market.get("gate", "green")

        # 觀察清單狀態
        watchlist_line = ""
        if in_watchlist_entry:
            added_date  = in_watchlist_entry.get("added_at", "")[:10]
            price_added = in_watchlist_entry.get("price_added", "N/A")
            watchlist_line = f"👁 已在觀察清單（加入於 {added_date}，${price_added}）\n"

        # 技術面標籤
        tech_tags = " ".join(
            f"{TAG_EMOJI.get(t, '')} {label}"
            for label, t in tech.get("tags", [])
        )
        fund_tags = " ".join(
            f"{TAG_EMOJI.get(t, '')} {label}"
            for label, t in fund.get("tags", [])
        )

        # 大盤警告
        gate_warn = ""
        if gate == "yellow":
            gate_warn = "\n⚠️ 大盤黃燈，建議倉位減半"
        elif gate == "red":
            gate_warn = "\n🔴 大盤紅燈，謹慎操作"

        msg = (
            f"━━━━━━━━━━━━━━━━\n"
            f"{watchlist_line}"
            f"📊 {ticker}  {short_name}\n"
            f"💰 ${price:.2f}  綜合評分：{composite}\n\n"
            f"📈 技術面 {tech_score}/100\n"
            f"  EMA排列 {tech['breakdown']['ema_alignment']}/25  "
            f"RS Line {tech['breakdown']['rs_line']}/20\n"
            f"  成交量 {tech['breakdown']['volume_struct']}/20  "
            f"形態 {tech['breakdown']['pattern']}/25  "
            f"ATR {tech['breakdown']['atr_risk']}/10\n"
            f"  {tech_tags}\n"
            f"  形態：{pattern_type} — {pattern_note}\n\n"
            f"💼 基本面 {fund_score}/100\n"
            f"  EPS加速 {fund['breakdown']['eps_acceleration']}/30  "
            f"營收 {fund['breakdown']['revenue_margin']}/25\n"
            f"  機構 {fund['breakdown']['institutional']}/25  "
            f"Sector {fund['breakdown']['sector_strength']}/20\n"
            f"  {fund_tags}\n\n"
            f"🎯 突破點 ${breakout or 'N/A'}\n"
            f"🛑 止損 ${stop_loss:.2f}（ATR {atr_risk:.1f}%）"
            f"{gate_warn}"
        )

        # Inline Keyboard 按鈕
        if in_watchlist_entry:
            keyboard = {
                "inline_keyboard": [[
                    {"text": "深度分析", "callback_data": f"deep_{ticker}"},
                    {"text": "移除觀察清單", "callback_data": f"remove_{ticker}"},
                ]]
            }
        else:
            keyboard = {
                "inline_keyboard": [[
                    {"text": "深度分析", "callback_data": f"deep_{ticker}"},
                    {"text": "加入觀察清單", "callback_data": f"add_{ticker}"},
                ]]
            }

        return msg, keyboard

    # ─── 發送 ────────────────────────────────────────────────

    def _send_message(self, text: str, reply_markup: dict = None):
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(
                f"{BASE_URL}/sendMessage",
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Telegram 發送失敗: {e}")

    def answer_callback(self, callback_query_id: str, text: str = ""):
        """回應按鈕點擊"""
        try:
            requests.post(
                f"{BASE_URL}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Callback 回應失敗: {e}")
