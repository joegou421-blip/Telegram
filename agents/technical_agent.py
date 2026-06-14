import numpy as np
import pandas as pd
import logging
from config.settings import (
    TECH_WEIGHTS, ATR_MULTIPLIER, MAX_ATR_RISK_PCT,
)
from data.indicators import (
    add_all_indicators, is_sma200_rising,
    calc_updown_volume_ratio, calc_volume_ratio,
    calc_rs_rating, calc_rs_score, calc_volume_dryness, calc_weekly_ema_alignment,
    calc_institutional_sweep, calc_pullback_score
)

logger = logging.getLogger(__name__)


class TechnicalAgent:

    def analyze(self, ticker: str, df: pd.DataFrame, spy_df: pd.DataFrame,
                universe_closes: dict = None, regime: str = "normal") -> dict:
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

        # ── 個股評級（A/B/C）木桶效應 ───────────────────────
        # 只有真正找到 VCP 形態（score > 0）才用 last_pullback_pct 評級
        # 找不到形態 → 視為 B 級（有基本形態但不夠完美）
        # 回測策略：用均線位置和量乾燥度判斷，不依賴 VCP 形態
        grade, grade_reason = self._calc_stock_grade(df, last, dryness)

        # ── 距 EMA20 距離（回測策略核心指標）─────────────────
        breakout_point = pattern_res.get("breakout_point")
        current_price  = last["close"]
        ema20_val      = last.get("ema20", 0)
        deviation_pct  = 0.0
        if ema20_val and ema20_val > 0:
            deviation_pct = (current_price - ema20_val) / ema20_val * 100

        # ── 機構掃貨痕跡 + 回測買入排序分 ─────────────────────
        sweep    = calc_institutional_sweep(df)
        pullback = calc_pullback_score(df, spy_rs=rs_rating, regime=regime)

        return {
            "ticker":  ticker,
            "total":   round(total),
            "grade":   grade,
            "grade_reason": grade_reason,
            "deviation_pct": round(deviation_pct, 2),
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
                "rs_raw_score":       calc_rs_score(df["close"], spy_df["close"] if spy_df is not None else None),
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
                "entry_price":        round(current_price, 2),
                "breakout_point":     breakout_point,
                "last_pullback_pct":  pattern_res.get("last_pullback_pct"),
                "institutional_sweep": sweep,
                "pullback":           pullback,
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
        return self.rs_rating_to_score(rating), rating

    @staticmethod
    def rs_rating_to_score(rating: float) -> float:
        """RS Rating (1-99) 換算成 RS breakdown 分數（滿分15）"""
        if rating >= 90:
            return 15
        elif rating >= 80:
            return 12
        elif rating >= 70:
            return 9
        elif rating >= 60:
            return 6
        elif rating >= 50:
            return 3
        else:
            return 0

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
        best["notes"] = best.get("notes", "形態不明顯")
        return best

    def _find_pivots(self, series, window: int = 5) -> list:
        """
        找價格序列的局部高點和低點（樞軸點）
        window: 左右各比較幾個點
        返回格式：[{"idx": i, "price": price, "type": "high"/"low"}, ...]
        """
        pivots = []
        values = series.values
        n      = len(values)

        for i in range(window, n - window):
            left  = values[i - window: i]
            right = values[i + 1: i + window + 1]
            val   = values[i]

            if val >= max(left) and val >= max(right):
                pivots.append({"idx": i, "price": val, "type": "high"})
            elif val <= min(left) and val <= min(right):
                pivots.append({"idx": i, "price": val, "type": "low"})

        # 只保留交替出現的高低點（去除連續同類型）
        filtered = []
        for p in pivots:
            if not filtered or filtered[-1]["type"] != p["type"]:
                filtered.append(p)
            else:
                # 同類型取極值
                if p["type"] == "high" and p["price"] > filtered[-1]["price"]:
                    filtered[-1] = p
                elif p["type"] == "low" and p["price"] < filtered[-1]["price"]:
                    filtered[-1] = p

        return filtered

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

        breakout_point   = recent["high"].max()
        last_pullback_pct = contractions[-1] * 100 if contractions else 999
        return {
            "score":            min(score, TECH_WEIGHTS["pattern"]),
            "notes":            " · ".join(notes_list),
            "contractions":     contractions,
            "breakout_point":   round(breakout_point, 2),
            "last_pullback_pct": round(last_pullback_pct, 1),
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
        denom = left_high_val - cup_low_val
        if denom <= 0:
            return {"score": 0, "notes": "杯子高低點數據異常，無法計算"}
        recovery = (current_price - cup_low_val) / denom
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

    def _calc_stock_grade(self, df, last, volume_dryness: float) -> tuple:
        """
        回測買入策略的個股分級（Pullback Trading）
        判斷股價是否在均線附近且賣壓竭盡

        A 級：多頭排列 + 距EMA20在±1%以內 + 量乾燥度 < 50%
        B 級：站上EMA50 + 距EMA20在1-3%以內 + 量乾燥度 50-65%
        C 級：跌破EMA50 OR 量乾燥度 > 65% OR 距均線過遠
        """
        vdr          = volume_dryness * 100
        price        = last["close"]
        ema20        = last.get("ema20", 0)
        ema50        = last.get("ema50", 0)

        # 距 EMA20 的距離（百分比）
        dist_ema20 = ((price - ema20) / ema20 * 100) if ema20 > 0 else 999
        dist_ema50 = ((price - ema50) / ema50 * 100) if ema50 > 0 else 999

        above_ema20 = price > ema20
        above_ema50 = price > ema50

        # ── C 級（一票否決）──────────────────────────────────
        if not above_ema50:
            return "C", f"跌破 EMA50，空頭結構，不做回測買入"

        if vdr > 65:
            return "C", f"量乾燥度 {vdr:.0f}% > 65%，賣壓未竭，繼續下跌風險高"

        if dist_ema20 > 5:
            return "C", f"距 EMA20 +{dist_ema20:.1f}%，股價過高，等待回落"

        if dist_ema20 < -3:
            return "C", f"距 EMA20 {dist_ema20:.1f}%，已跌破均線過深，趨勢破壞"

        # ── A 級（完美回測）──────────────────────────────────
        if above_ema20 and above_ema50 and abs(dist_ema20) <= 1 and vdr < 50:
            return "A", (
                f"完美回測：多頭排列，距EMA20 {dist_ema20:+.1f}%，"
                f"量乾燥度 {vdr:.0f}%（賣壓竭盡）"
            )

        # EMA50 回測（股價跌到 EMA50 附近）
        if above_ema50 and abs(dist_ema50) <= 1 and vdr < 50:
            return "A", (
                f"EMA50 回測：距EMA50 {dist_ema50:+.1f}%，"
                f"量乾燥度 {vdr:.0f}%（關鍵支撐位買入）"
            )

        # ── B 級（可接受的回測）──────────────────────────────
        if above_ema50 and abs(dist_ema20) <= 3 and vdr <= 65:
            reasons = []
            reasons.append(f"距EMA20 {dist_ema20:+.1f}%")
            if vdr >= 50:
                reasons.append(f"量乾燥度 {vdr:.0f}%（略高）")
            return "B", "回測可接受：" + "，".join(reasons)

        return "C", f"條件不符：距EMA20 {dist_ema20:+.1f}%，量乾燥度 {vdr:.0f}%"

    def calc_position_size(self, grade: str, market_risk: str,
                            deviation_pct: float) -> dict:
        """
        回測買入策略的倉位計算
        deviation_pct = 距 EMA20 的距離（負數 = 在均線下方，正數 = 在均線上方）
        market_risk: 'low'(BULL_NORMAL) / 'medium'(BULL_HOT等) / 'high'(BEAR等)
        """
        # 一票否決：大盤高風險
        if market_risk == "high":
            return {
                "base_pct":  0,
                "multiplier": 0,
                "final_pct": 0,
                "reason":    "大盤高風險，一票否決不開倉",
            }

        # C 級直接放棄
        if grade == "C":
            return {
                "base_pct":  0,
                "multiplier": 0,
                "final_pct": 0,
                "reason":    "個股 C 級，條件不符，放棄",
            }

        # 基礎倉位矩陣（回測策略止損窄，可以用較大倉位）
        base_map = {
            ("A", "low"):    10.0,
            ("A", "medium"): 5.0,
            ("B", "low"):    5.0,
            ("B", "medium"): 2.5,
        }
        base_pct = base_map.get((grade, market_risk), 0)

        # 距 EMA20 距離乘數
        # 越接近均線，止損越窄，R:R 越好，倉位越大
        abs_dist = abs(deviation_pct)
        if abs_dist <= 1:
            multiplier  = 1.0
            mult_reason = f"距EMA20 {deviation_pct:+.1f}%，極佳入場點，正常倉位"
        elif abs_dist <= 2:
            multiplier  = 0.75
            mult_reason = f"距EMA20 {deviation_pct:+.1f}%，良好入場點，倉位 75%"
        elif abs_dist <= 3:
            multiplier  = 0.5
            mult_reason = f"距EMA20 {deviation_pct:+.1f}%，尚可入場，倉位減半"
        else:
            return {
                "base_pct":  0,
                "multiplier": 0,
                "final_pct": 0,
                "reason":    f"距EMA20 {deviation_pct:+.1f}% 超過 3%，不在回測買入窗口",
            }

        final_pct = round(base_pct * multiplier, 1)
        return {
            "base_pct":    base_pct,
            "multiplier":  multiplier,
            "final_pct":   final_pct,
            "mult_reason": mult_reason,
            "reason":      f"個股{grade}級 + 大盤{market_risk}風險 → {base_pct}% × {multiplier} = {final_pct}%",
        }

    def calc_trade_management(self, entry_price: float, stop_loss: float) -> dict:
        """
        回測買入策略的持倉管理（止損極窄，R:R 高）
        - 止損：EMA20/EMA50 下方 1.5-3%（已在 _score_atr 計算）
        - 帳面 +8% → 止損移至保本（因止損窄，更快保本）
        - 帳面 +15% → 賣出 1/3 鎖利
        - 帳面 +25% → 再賣 1/3
        - 剩餘倉位 → EMA20 移動停利
        """
        if entry_price <= 0:
            return {}
        initial_risk     = round((entry_price - stop_loss) / entry_price * 100, 1) if stop_loss > 0 else 0
        breakeven_trigger = round(entry_price * 1.08, 2)
        profit_target_1   = round(entry_price * 1.15, 2)
        profit_target_2   = round(entry_price * 1.25, 2)
        return {
            "entry":             round(entry_price, 2),
            "stop_loss":         round(stop_loss, 2),
            "initial_risk_pct":  initial_risk,
            "breakeven_trigger": breakeven_trigger,
            "profit_target_1":   profit_target_1,
            "profit_target_2":   profit_target_2,
            "management_plan": [
                f"初始止損：${stop_loss:.2f}（風險 {initial_risk:.1f}%，設在均線下方）",
                f"股價觸及 ${breakeven_trigger} (+8%) → 止損移至保本 ${entry_price:.2f}",
                f"股價觸及 ${profit_target_1} (+15%) → 賣出 1/3 倉位鎖利",
                f"股價觸及 ${profit_target_2} (+25%) → 再賣 1/3 倉位",
                "剩餘 1/3 倉位以 EMA20 作移動停利，收盤跌破 EMA20 全數出場",
            ],
        }

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

