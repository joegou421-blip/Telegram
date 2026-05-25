import numpy as np
import pandas as pd
import logging
import requests
from config.settings import (
    TECH_WEIGHTS, ATR_MULTIPLIER, MAX_ATR_RISK_PCT,
    AI_MODEL_FAST, OPENROUTER_API_KEY
)
from data.indicators import (
    add_all_indicators, is_sma200_rising,
    calc_updown_volume_ratio, calc_volume_ratio,
    calc_rs_rating, calc_volume_dryness, calc_weekly_ema_alignment
)

logger = logging.getLogger(__name__)


class TechnicalAgent:

    def analyze(self, ticker: str, df: pd.DataFrame, spy_df: pd.DataFrame,
                universe_closes: dict = None) -> dict:
        """
        完整技術面分析
        universe_closes: 所有股票的收盤價序列，用於計算 RS Rating 百分位
        """
        df = add_all_indicators(df, spy_df)
        last = df.iloc[-1]

        ema_score              = self._score_ema(df, last)
        rs_score, rs_rating    = self._score_rs_rating(df, spy_df)
        weekly_score, weekly   = self._score_weekly_trend(df)
        vol_score              = self._score_volume(df)
        dryness_score, dryness = self._score_volume_dryness(df)
        pattern_res            = self._score_pattern(ticker, df)
        atr_score, stop_loss, atr_risk_pct = self._score_atr(last)

        total = (ema_score + rs_score + weekly_score +
                 vol_score + dryness_score + pattern_res["score"] + atr_score)

        return {
            "ticker":  ticker,
            "total":   round(total),
            "breakdown": {
                "ema_alignment":  ema_score,
                "rs_rating":      rs_score,
                "weekly_trend":   weekly_score,
                "volume_struct":  vol_score,
                "volume_dryness": dryness_score,
                "pattern":        pattern_res["score"],
                "atr_risk":       atr_score,
            },
            "details": {
                "ema_layers_ok":      self._count_ema_layers(last),
                "sma200_rising":      is_sma200_rising(df),
                "rs_rating":          rs_rating,
                "rs_line_new_high":   self._rs_new_high(df),
                "weekly_aligned":     weekly.get("aligned", False),
                "weekly_ema10w":      weekly.get("ema10w"),
                "updown_vol_ratio":   calc_updown_volume_ratio(df),
                "volume_trend":       calc_volume_ratio(df),
                "volume_dryness":     dryness,
                "pattern_type":       pattern_res["type"],
                "pattern_notes":      pattern_res["notes"],
                "atr_risk_pct":       round(atr_risk_pct * 100, 1),
                "stop_loss":          round(stop_loss, 2),
                "entry_price":        round(last["close"], 2),
                "breakout_point":     pattern_res.get("breakout_point"),
            },
            "tags": self._build_tags(
                ema_score, rs_rating, weekly, vol_score,
                dryness, pattern_res, atr_risk_pct
            ),
        }

    # ─── EMA 排列（滿分 20）──────────────────────────────────

    def _score_ema(self, df, last) -> float:
        max_per_layer = TECH_WEIGHTS["ema_alignment"] / 5
        checks = [
            last["close"]  > last["ema20"],
            last["ema20"]  > last["ema50"],
            last["ema50"]  > last["ema150"],
            last["ema150"] > last["ema200"],
            is_sma200_rising(df),
        ]
        score = sum(max_per_layer for c in checks if c)
        dist_pct = (last["close"] - last["ema20"]) / last["ema20"]
        if dist_pct > 0.15:
            score -= 4
        elif dist_pct > 0.10:
            score -= 2
        return max(0, score)

    # ─── RS Rating（滿分 15）─────────────────────────────────

    def _score_rs_rating(self, df, spy_df) -> tuple:
        if spy_df is None:
            return 7.5, 50.0
        rating = calc_rs_rating(df["close"], spy_df["close"])
        if rating >= 90:
            score = 15
        elif rating >= 80:
            score = 12
        elif rating >= 70:
            score = 9
        elif rating >= 60:
            score = 6
        elif rating >= 50:
            score = 3
        else:
            score = 0
        return score, rating

    # ─── 週線趨勢確認（滿分 10）──────────────────────────────

    def _score_weekly_trend(self, df) -> tuple:
        weekly = calc_weekly_ema_alignment(df)
        if weekly.get("aligned"):
            score = TECH_WEIGHTS["weekly_trend"]
        else:
            score = TECH_WEIGHTS["weekly_trend"] * 0.3
        return score, weekly

    # ─── 成交量結構（滿分 20）────────────────────────────────

    def _score_volume(self, df) -> float:
        score = 0
        ud_ratio = calc_updown_volume_ratio(df)
        if ud_ratio >= 2.0:
            score += 10
        elif ud_ratio >= 1.5:
            score += 7
        elif ud_ratio >= 1.0:
            score += 4

        vol_trend = calc_volume_ratio(df)
        if vol_trend >= 1.2:
            score += 10
        elif vol_trend >= 1.0:
            score += 6
        elif vol_trend >= 0.8:
            score += 3

        return min(score, TECH_WEIGHTS["volume_struct"])

    # ─── 成交量乾燥度（滿分 10）──────────────────────────────

    def _score_volume_dryness(self, df) -> tuple:
        dryness = calc_volume_dryness(df)
        if dryness <= 0.50:
            score = 10
        elif dryness <= 0.65:
            score = 8
        elif dryness <= 0.80:
            score = 5
        elif dryness <= 1.00:
            score = 2
        else:
            score = 0
        return score, dryness

    # ─── 形態識別（滿分 20）──────────────────────────────────

    def _score_pattern(self, ticker, df) -> dict:
        vcp = self._detect_vcp(df)
        cnh = self._detect_cup_and_handle(df)
        best = vcp if vcp["score"] >= cnh["score"] else cnh
        best["type"] = "VCP" if vcp["score"] >= cnh["score"] else "Cup & Handle"

        if best["score"] >= 12 and OPENROUTER_API_KEY:
            best["notes"] = self._ai_pattern_review(ticker, df, best)
        else:
            best["notes"] = best.get("notes", "形態不明顯")

        return best

    def _detect_vcp(self, df) -> dict:
        if len(df) < 60:
            return {"score": 0, "notes": "數據不足"}
        recent = df.tail(126)
        pivots = self._find_pivots(recent["close"])
        if len(pivots) < 4:
            return {"score": 0, "notes": "找不到足夠樞軸點"}

        contractions = []
        for i in range(0, len(pivots) - 1, 2):
            if i + 1 < len(pivots):
                pullback = (pivots[i]["price"] - pivots[i+1]["price"]) / pivots[i]["price"]
                contractions.append(pullback)

        if len(contractions) < 2:
            return {"score": 0, "notes": "回調次數不足"}

        score = 0
        notes_list = []

        is_contracting = all(
            contractions[i] > contractions[i+1]
            for i in range(len(contractions) - 1)
        )
        if is_contracting:
            score += 10
            notes_list.append(f"回調遞減: {[f'{c*100:.1f}%' for c in contractions]}")
        else:
            score += 3

        if contractions[-1] < 0.08:
            score += 8
            notes_list.append(f"最後回調 {contractions[-1]*100:.1f}%（極度緊縮）")
        elif contractions[-1] < 0.15:
            score += 5
            notes_list.append(f"最後回調 {contractions[-1]*100:.1f}%（緊縮良好）")

        breakout_point = recent["high"].max()
        return {
            "score":          min(score, TECH_WEIGHTS["pattern"]),
            "notes":          " · ".join(notes_list),
            "contractions":   contractions,
            "breakout_point": round(breakout_point, 2),
        }

    def _detect_cup_and_handle(self, df) -> dict:
        if len(df) < 100:
            return {"score": 0, "notes": "數據不足"}
        recent = df.tail(252)
        closes = recent["close"].values
        n = len(closes)

        left_high_idx = np.argmax(closes[:n // 2])
        left_high_val = closes[left_high_idx]
        cup_low_val   = closes[left_high_idx:].min()
        cup_depth     = (left_high_val - cup_low_val) / left_high_val

        score = 0
        notes_list = []

        if 0.15 <= cup_depth <= 0.35:
            score += 10
            notes_list.append(f"杯子深度 {cup_depth*100:.1f}%（理想）")
        elif 0.10 <= cup_depth <= 0.40:
            score += 5
            notes_list.append(f"杯子深度 {cup_depth*100:.1f}%（可接受）")
        else:
            notes_list.append(f"杯子深度 {cup_depth*100:.1f}%（不理想）")

        current_price = closes[-1]
        recovery = (current_price - cup_low_val) / (left_high_val - cup_low_val)
        if recovery >= 0.90:
            score += 10
            notes_list.append("右側回升至高點附近")
        elif recovery >= 0.75:
            score += 6
            notes_list.append(f"右側回升 {recovery*100:.0f}%")

        return {
            "score":          min(score, TECH_WEIGHTS["pattern"]),
            "notes":          " · ".join(notes_list),
            "cup_depth":      cup_depth,
            "breakout_point": round(left_high_val, 2),
        }

    # ─── ATR 止損（滿分 5）───────────────────────────────────

    def _score_atr(self, last) -> tuple:
        entry_price = last["close"]
        atr         = last["atr14"]
        stop_loss   = entry_price - (atr * ATR_MULTIPLIER)
        risk_pct    = (entry_price - stop_loss) / entry_price

        if risk_pct <= 0.04:
            score = 5
        elif risk_pct <= 0.06:
            score = 4
        elif risk_pct <= 0.08:
            score = 2
        else:
            score = 0

        return score, stop_loss, risk_pct

    # ─── AI 形態複核 ──────────────────────────────────────────

    def _ai_pattern_review(self, ticker, df, pattern) -> str:
        try:
            recent = df.tail(20)
            price_data = [
                f"{row.name.strftime('%Y-%m-%d')}: 收{row['close']:.2f} 量{int(row['volume'])}"
                for _, row in recent.iterrows()
            ]
            prompt = (
                f"你是專業的 swing trader。分析 {ticker} 的 {pattern['type']} 形態。\n"
                f"數學分析：{pattern['notes']}\n"
                f"近20天數據：\n" + "\n".join(price_data) +
                f"\n\n用1-2句繁體中文說明形態品質和最值得注意的地方。不要重複數字，直接說結論。"
            )
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://swing-trade-agent.github.io",
                    "X-Title":       "Swing Trade Agent",
                },
                json={
                    "model":      AI_MODEL_FAST,
                    "max_tokens": 150,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"AI 形態複核失敗: {e}")
            return pattern.get("notes", "")

    # ─── 輔助 ────────────────────────────────────────────────

    def _find_pivots(self, series, window=5):
        pivots = []
        values = series.values
        for i in range(window, len(values) - window):
            segment = values[i - window: i + window + 1]
            if values[i] == segment.max():
                pivots.append({"idx": i, "price": values[i], "type": "high"})
            elif values[i] == segment.min():
                pivots.append({"idx": i, "price": values[i], "type": "low"})
        return pivots

    def _count_ema_layers(self, last) -> int:
        return sum([
            last["close"]  > last["ema20"],
            last["ema20"]  > last["ema50"],
            last["ema50"]  > last["ema150"],
            last["ema150"] > last["ema200"],
        ])

    def _rs_new_high(self, df) -> bool:
        if "rs_line" not in df.columns:
            return False
        rs = df["rs_line"].dropna()
        if len(rs) < 20:
            return False
        return rs.iloc[-1] >= rs.tail(252).max() * 0.98

    def _build_tags(self, ema_score, rs_rating, weekly, vol_score,
                    dryness, pattern_res, atr_risk_pct) -> list:
        tags = []
        if ema_score >= 16:
            tags.append(("EMA 完美排列", "ok"))
        elif ema_score >= 12:
            tags.append(("EMA 大致排列", "warn"))
        else:
            tags.append(("EMA 排列不佳", "bad"))

        if rs_rating >= 85:
            tags.append((f"RS Rating {rs_rating}（極強）", "ok"))
        elif rs_rating >= 70:
            tags.append((f"RS Rating {rs_rating}（強勢）", "ok"))
        elif rs_rating >= 50:
            tags.append((f"RS Rating {rs_rating}（中等）", "warn"))
        else:
            tags.append((f"RS Rating {rs_rating}（偏弱）", "bad"))

        if weekly.get("aligned"):
            tags.append(("週線趨勢確認", "ok"))
        else:
            tags.append(("週線趨勢未確認", "warn"))

        if dryness <= 0.65:
            tags.append((f"成交量乾燥 {dryness:.2f}x（籌碼穩定）", "ok"))
        elif dryness >= 1.2:
            tags.append((f"成交量偏高 {dryness:.2f}x", "warn"))

        if pattern_res["score"] >= 15:
            tags.append((f"{pattern_res['type']} 形態完整", "ok"))
        elif pattern_res["score"] >= 8:
            tags.append((f"{pattern_res['type']} 形態中等", "warn"))

        if atr_risk_pct > 0.08:
            tags.append(("止損距離過大", "bad"))

        return tags
