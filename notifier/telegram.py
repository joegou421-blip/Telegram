import requests
import logging
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
GATE_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
TAG_EMOJI  = {"ok": "✅", "warn": "⚠️", "bad": "❌"}


class TelegramNotifier:

    def send_daily_report(self, scan_result: dict):
        market     = scan_result["market"]
        candidates = scan_result["candidates"]

        self._send(self._format_market_header(market))

        if not candidates:
            self._send("今日無符合條件的候選股。")
            return

        for stock in candidates[:15]:
            watchlist_entry = None
            msg, keyboard  = self._format_stock_card(stock, market, watchlist_entry)
            self._send(msg, keyboard)

    def send_stock_card(self, result: dict, in_watchlist_entry):
        market        = result.get("market", {})
        msg, keyboard = self._format_stock_card(result, market, in_watchlist_entry)
        self._send(msg, keyboard)

    def send_error_message(self, text: str):
        self._send(f"❌ {text}")

    def send_watchlist_summary(self, watchlist: list):
        if not watchlist:
            self._send("你的觀察清單目前是空的。")
            return
        lines = [f"👁 觀察清單（{len(watchlist)} 隻）\n{'─'*20}"]
        for item in watchlist:
            lines.append(
                f"\n📊 {item['ticker']}\n"
                f"   加入：{item.get('added_at','')[:10]}  "
                f"價格：${item.get('price_added','N/A')}\n"
                f"   技術 {item.get('tech_score','N/A')} · "
                f"基本面 {item.get('fund_score','N/A')}"
            )
        self._send("\n".join(lines))

    def notify_watchlist_alert(self, ticker: str, alert_type: str, detail: str):
        icons = {
            "take_profit": "💰",
            "breakdown":   "🚨",
            "breakout":    "🎯",
        }
        icon = icons.get(alert_type, "📢")
        self._send(f"{icon} {ticker} 提醒\n{detail}")

    # ─── 格式化 ──────────────────────────────────────────────

    def _format_market_header(self, market: dict) -> str:
        gate    = market["gate"]
        emoji   = GATE_EMOJI.get(gate, "⚪")
        summary = market.get("summary", "")
        ftd     = market.get("ftd", {})
        advice  = {
            "green":  "正常倉位進場",
            "yellow": "建議倉位減半",
            "red":    "暫停做多，等待觀望",
        }.get(gate, "")
        ftd_line = f"\n🎯 Follow-Through Day 確認！{ftd.get('notes','')}" if ftd.get("detected") else ""
        return f"{emoji} {summary}\n建議：{advice}{ftd_line}"

    def _format_stock_card(self, result: dict, market: dict,
                            in_watchlist_entry) -> tuple:
        ticker     = result["ticker"]
        short_name = result.get("short_name", ticker)
        price      = result["price"]
        tech       = result["tech"]
        fund       = result["fund"]
        composite  = result["composite_score"]
        tech_score = result["tech_score"]
        fund_score = result["fund_score"]
        consecutive = result.get("consecutive_days", 0)

        t       = tech["breakdown"]
        details = tech["details"]
        f       = fund["breakdown"]

        stop_loss    = details.get("stop_loss", 0)
        atr_risk     = details.get("atr_risk_pct", 0)
        breakout     = details.get("breakout_point")
        pattern_type = details.get("pattern_type", "")
        pattern_note = details.get("pattern_notes", "")
        rs_rating    = details.get("rs_rating", 0)
        weekly_ok    = details.get("weekly_aligned", False)
        dryness      = details.get("volume_dryness", 1.0)
        gate         = market.get("gate", "green")

        # 觀察清單標記
        watchlist_line = ""
        if in_watchlist_entry:
            added_date  = in_watchlist_entry.get("added_at", "")[:10]
            price_added = in_watchlist_entry.get("price_added", "N/A")
            gain        = ""
            if price_added and price_added != "N/A":
                try:
                    g = (price - float(price_added)) / float(price_added) * 100
                    gain = f"  盈虧 {g:+.1f}%"
                except Exception:
                    pass
            watchlist_line = f"👁 已在觀察清單（{added_date}，${price_added}{gain}）\n"

        # 連續出現標記
        consecutive_line = ""
        if consecutive >= 2:
            consecutive_line = f"🔥 連續第 {consecutive} 天出現在候選清單\n"

        # 技術面標籤
        tech_tags = " ".join(
            f"{TAG_EMOJI.get(t_type,'')} {label}"
            for label, t_type in tech.get("tags", [])
        )

        # 大盤警告
        gate_warn = {
            "yellow": "\n⚠️ 大盤黃燈，建議倉位減半",
            "red":    "\n🔴 大盤紅燈，謹慎操作",
        }.get(gate, "")

        msg = (
            f"━━━━━━━━━━━━━━━━\n"
            f"{watchlist_line}"
            f"{consecutive_line}"
            f"📊 {ticker}  {short_name}\n"
            f"💰 ${price:.2f}  綜合評分：{composite}\n\n"
            f"📈 技術面 {tech_score}/100\n"
            f"  EMA {t['ema_alignment']}/20  "
            f"RS Rating {rs_rating:.0f}（{t['rs_rating']}/20）  "
            f"週線 {'✅' if weekly_ok else '❌'}（{t['weekly_trend']}/10）\n"
            f"  成交量 {t['volume_struct']}/20  "
            f"乾燥度 {dryness:.2f}x（{t['volume_dryness']}/10）\n"
            f"  形態({pattern_type}) {t['pattern']}/20\n"
            f"  {tech_tags}\n"
            f"  {pattern_note}\n\n"
            f"💼 基本面 {fund_score}/100\n"
            f"  EPS加速 {f['eps_acceleration']}/30  "
            f"營收 {f['revenue_margin']}/25\n"
            f"  機構 {f['institutional']}/25  "
            f"Sector {f['sector_strength']}/20\n\n"
            f"🎯 突破點 ${breakout or 'N/A'}\n"
            f"🛑 止損 ${stop_loss:.2f}（ATR {atr_risk:.1f}%）"
            f"{gate_warn}"
        )

        if in_watchlist_entry:
            keyboard = {"inline_keyboard": [[
                {"text": "🔍 深度分析",     "callback_data": f"deep_{ticker}"},
                {"text": "🗑 移除觀察清單", "callback_data": f"remove_{ticker}"},
            ]]}
        else:
            keyboard = {"inline_keyboard": [[
                {"text": "🔍 深度分析",     "callback_data": f"deep_{ticker}"},
                {"text": "👁 加入觀察清單", "callback_data": f"add_{ticker}"},
            ]]}

        return msg, keyboard

    # ─── 發送 ────────────────────────────────────────────────

    def _send(self, text: str, reply_markup: dict = None):
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
        try:
            requests.post(
                f"{BASE_URL}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Callback 回應失敗: {e}")
