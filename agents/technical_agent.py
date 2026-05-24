import numpy as np
import pandas as pd
import logging
from config.settings import TECH_WEIGHTS, ATR_MULTIPLIER, MAX_ATR_RISK_PCT, AI_MODEL, OPENROUTER_API_KEY
from data.indicators import (
    add_all_indicators, is_sma200_rising,
    calc_updown_volume_ratio, calc_volume_ratio
)
import requests

logger = logging.getLogger(__name__)


class TechnicalAgent:

    def analyze(self, ticker: str, df: pd.DataFrame, spy_df: pd.DataFrame) -> dict:
        """完整技術面分析，回傳評分和細節"""
        df = add_all_indicators(df, spy_df)
        last = df.iloc[-1]

        ema_score    = self._score_ema(df, last)
        rs_score     = self._score_rs_line(df)
        vol_score    = self._score_volume(df)
        pattern_res  = self._score_pattern(ticker, df)
        atr_score, stop_loss, atr_risk_pct = self._score_atr(last)

        total = ema_score + rs_score + vol_score + pattern_res["score"] + atr_score

        return {
            "ticker":         ticker,
            "total":          round(total),
            "breakdown": {
                "ema_alignment": ema_score,
                "rs_line":       rs_score,
                "volume_struct": vol_score,
                "pattern":       pattern_res["score"],
                "atr_risk":      atr_score,
            },
            "details": {
                "ema_layers_ok":    self._count_ema_layers(last),
                "sma200_rising":    is_sma200_rising(df),
                "rs_line_new_high": self._rs_new_high(df),
                "updown_vol_ratio": calc_updown_volume_ratio(df),
                "volume_trend":     calc_volume_ratio(df),
                "pattern_type":     pattern_res["type"],
                "pattern_notes":    pattern_res["notes"],
                "atr_risk_pct":     round(atr_risk_pct * 100, 1),
                "stop_loss":        round(stop_loss, 2),
                "entry_price":      round(last["close"], 2),
                "breakout_point":   pattern_res.get("breakout_point"),
            },
            "tags": self._build_tags(ema_score, rs_score, vol_score, pattern_res, atr_risk_pct),
        }

    # ─── EMA 排列評分（滿分 25）────────────────────────────────

    def _score_ema(self, df: pd.DataFrame, last: pd.Series) -> float:
        score = 0
        max_per_layer = TECH_WEIGHTS["ema_alignment"] / 5  # 每層 5 分

        # 五層檢查
        checks = [
            last["close"]  > last["ema20"],
            last["ema20"]  > last["ema50"],
            last["ema50"]  > last["ema150"],
            last["ema150"] > last["ema200"],
            is_sma200_rising(df),
        ]
        score = sum(max_per_layer for c in checks if c)

        # 距離 EMA20 不能太遠（超過 15% 扣分）
        dist_pct = (last["close"] - last["ema20"]) / last["ema20"]
        if dist_pct > 0.15:
            score -= 5
        elif dist_pct > 0.10:
            score -= 2

        return max(0, score)

    # ─── RS Line 評分（滿分 20）────────────────────────────────

    def _score_rs_line(self, df: pd.DataFrame) -> float:
        if "rs_line" not in df.columns:
            return 10  # 無法計算給中等分

        rs = df["rs_line"].dropna()
        if len(rs) < 20:
            return 10

        score = 0
        rs52w_high = rs.tail(252).max()

        # RS Line 方向（近 20 天斜率）
        if rs.iloc[-1] > rs.iloc[-20]:
            score += 10

        # RS Line 創 52 週新高
        if rs.iloc[-1] >= rs52w_high * 0.98:
            score += 10

        return score

    # ─── 成交量結構評分（滿分 20）──────────────────────────────

    def _score_volume(self, df: pd.DataFrame) -> float:
        score = 0

        # Up/Down Volume Ratio
        ud_ratio = calc_updown_volume_ratio(df)
        if ud_ratio >= 2.0:
            score += 10
        elif ud_ratio >= 1.5:
            score += 7
        elif ud_ratio >= 1.0:
            score += 4

        # 近期均量 vs 長期均量
        vol_trend = calc_volume_ratio(df)
        if vol_trend >= 1.2:
            score += 10
        elif vol_trend >= 1.0:
            score += 6
        elif vol_trend >= 0.8:
            score += 3

        return min(score, TECH_WEIGHTS["volume_struct"])

    # ─── 形態識別評分（滿分 25）────────────────────────────────

    def _score_pattern(self, ticker: str, df: pd.DataFrame) -> dict:
        vcp_result = self._detect_vcp(df)
        cnh_result = self._detect_cup_and_handle(df)

        # 選評分較高的
        if vcp_result["score"] >= cnh_result["score"]:
            best = vcp_result
            best["type"] = "VCP"
        else:
            best = cnh_result
            best["type"] = "Cup & Handle"

        # 用 AI 補充判斷（只有數學分 >= 15 才值得花 API）
        if best["score"] >= 15 and OPENROUTER_API_KEY:
            ai_notes = self._ai_pattern_review(ticker, df, best)
            best["notes"] = ai_notes
        else:
            best["notes"] = best.get("notes", "形態不明顯")

        return best

    def _detect_vcp(self, df: pd.DataFrame) -> dict:
        """VCP 量化識別"""
        closes  = df["close"].values
        volumes = df["volume"].values
        n = len(closes)
        if n < 60:
            return {"score": 0, "notes": "數據不足"}

        # 找過去 6 個月的局部高點和低點
        recent = df.tail(126)
        pivots = self._find_pivots(recent["close"])

        if len(pivots) < 4:
            return {"score": 0, "notes": "找不到足夠的樞軸點"}

        # 計算每次回調幅度
        contractions = []
        for i in range(0, len(pivots) - 1, 2):
            if i + 1 < len(pivots):
                high_val = pivots[i]["price"]
                low_val  = pivots[i + 1]["price"]
                pullback = (high_val - low_val) / high_val
                contractions.append(pullback)

        if len(contractions) < 2:
            return {"score": 0, "notes": "回調次數不足"}

        score = 0
        notes_list = []

        # 緊縮性：每次回調幅度遞減
        is_contracting = all(
            contractions[i] > contractions[i + 1]
            for i in range(len(contractions) - 1)
        )
        if is_contracting:
            score += 12
            notes_list.append(f"回調幅度遞減: {[f'{c*100:.1f}%' for c in contractions]}")
        else:
            score += 4
            notes_list.append("回調幅度未完全遞減")

        # 最後一次回調 < 15%
        if contractions[-1] < 0.10:
            score += 8
            notes_list.append(f"最後回調僅 {contractions[-1]*100:.1f}%（非常緊縮）")
        elif contractions[-1] < 0.15:
            score += 5
            notes_list.append(f"最後回調 {contractions[-1]*100:.1f}%（緊縮良好）")

        # 突破點（前高）
        breakout_point = recent["high"].max()

        return {
            "score":          min(score, TECH_WEIGHTS["pattern"]),
            "notes":          " · ".join(notes_list),
            "contractions":   contractions,
            "breakout_point": round(breakout_point, 2),
        }

    def _detect_cup_and_handle(self, df: pd.DataFrame) -> dict:
        """Cup & Handle 量化識別"""
        if len(df) < 100:
            return {"score": 0, "notes": "數據不足"}

        recent = df.tail(252)
        closes = recent["close"].values
        n = len(closes)

        # 找左側高點
        left_high_idx  = np.argmax(closes[:n // 2])
        left_high_val  = closes[left_high_idx]
        cup_low_val    = closes[left_high_idx:].min()
        cup_depth      = (left_high_val - cup_low_val) / left_high_val

        score = 0
        notes_list = []

        # 杯子深度 15-35%
        if 0.15 <= cup_depth <= 0.35:
            score += 10
            notes_list.append(f"杯子深度 {cup_depth*100:.1f}%（理想）")
        elif 0.10 <= cup_depth <= 0.40:
            score += 5
            notes_list.append(f"杯子深度 {cup_depth*100:.1f}%（可接受）")
        else:
            notes_list.append(f"杯子深度 {cup_depth*100:.1f}%（不理想）")

        # 右側回升到左側高點附近
        right_side     = closes[left_high_idx + int(n * 0.3):]
        current_price  = closes[-1]
        recovery       = (current_price - cup_low_val) / (left_high_val - cup_low_val)

        if recovery >= 0.90:
            score += 10
            notes_list.append("右側已回升至高點附近")
        elif recovery >= 0.75:
            score += 6
            notes_list.append(f"右側回升 {recovery*100:.0f}%")

        breakout_point = left_high_val

        return {
            "score":          min(score, TECH_WEIGHTS["pattern"]),
            "notes":          " · ".join(notes_list),
            "cup_depth":      cup_depth,
            "breakout_point": round(breakout_point, 2),
        }

    # ─── ATR 止損評分（滿分 10）────────────────────────────────

    def _score_atr(self, last: pd.Series):
        entry_price = last["close"]
        atr         = last["atr14"]
        stop_loss   = entry_price - (atr * ATR_MULTIPLIER)
        risk_pct    = (entry_price - stop_loss) / entry_price

        if risk_pct <= 0.05:
            score = 10
        elif risk_pct <= 0.08:
            score = 6
        else:
            score = 0  # 風險太大

        return score, stop_loss, risk_pct

    # ─── AI 形態複核 ────────────────────────────────────────────

    def _ai_pattern_review(self, ticker: str, df: pd.DataFrame, pattern: dict) -> str:
        """用 AI 對形態做文字評估"""
        try:
            recent = df.tail(20)
            price_data = []
            for _, row in recent.iterrows():
                price_data.append(
                    f"{row.name.strftime('%Y-%m-%d')}: "
                    f"收{row['close']:.2f} 量{int(row['volume'])}"
                )

            prompt = (
                f"你是專業的 swing trader。分析 {ticker} 的 {pattern['type']} 形態。\n\n"
                f"數學分析結果：{pattern['notes']}\n\n"
                f"近20天價量數據：\n" + "\n".join(price_data) + "\n\n"
                f"請用 1-2 句繁體中文說明這個形態的品質和最值得注意的地方。"
                f"不要重複數字，直接說結論。"
            )

            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "max_tokens": 150,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

        except Exception as e:
            logger.warning(f"AI 形態複核失敗: {e}")
            return pattern.get("notes", "")

    # ─── 輔助方法 ────────────────────────────────────────────────

    def _find_pivots(self, series: pd.Series, window: int = 5) -> list:
        """找局部高低點"""
        pivots = []
        values = series.values
        for i in range(window, len(values) - window):
            segment = values[i - window: i + window + 1]
            if values[i] == segment.max():
                pivots.append({"idx": i, "price": values[i], "type": "high"})
            elif values[i] == segment.min():
                pivots.append({"idx": i, "price": values[i], "type": "low"})
        return pivots

    def _count_ema_layers(self, last: pd.Series) -> int:
        checks = [
            last["close"]  > last["ema20"],
            last["ema20"]  > last["ema50"],
            last["ema50"]  > last["ema150"],
            last["ema150"] > last["ema200"],
        ]
        return sum(checks)

    def _rs_new_high(self, df: pd.DataFrame) -> bool:
        if "rs_line" not in df.columns:
            return False
        rs = df["rs_line"].dropna()
        if len(rs) < 20:
            return False
        return rs.iloc[-1] >= rs.tail(252).max() * 0.98

    def _build_tags(self, ema_score, rs_score, vol_score, pattern_res, atr_risk_pct) -> list:
        tags = []
        if ema_score >= 20:
            tags.append(("EMA 完美排列", "ok"))
        elif ema_score >= 15:
            tags.append(("EMA 大致排列", "warn"))
        else:
            tags.append(("EMA 排列不佳", "bad"))

        if rs_score >= 15:
            tags.append(("RS Line 創新高", "ok"))
        elif rs_score >= 10:
            tags.append(("RS Line 向上", "ok"))
        else:
            tags.append(("RS Line 偏弱", "warn"))

        if pattern_res["score"] >= 20:
            tags.append((f"{pattern_res['type']} 形態完整", "ok"))
        elif pattern_res["score"] >= 12:
            tags.append((f"{pattern_res['type']} 形態中等", "warn"))

        if atr_risk_pct > 0.08:
            tags.append(("止損距離偏大", "bad"))

        return tags
