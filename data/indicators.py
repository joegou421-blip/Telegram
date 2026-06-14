import pandas as pd
import numpy as np


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calc_rs_line(stock_close: pd.Series, spy_close: pd.Series) -> pd.Series:
    aligned = pd.DataFrame({"stock": stock_close, "spy": spy_close}).dropna()
    return aligned["stock"] / aligned["spy"]


def calc_rs_score(stock_close, spy_close=None) -> float:
    """
    原始動能得分（歐尼爾標準）
    狀態A - 正常股（>252天且無異常）：近3個月40% + 過去9個月60%
    狀態B - 數據中毒（漲幅>300%）：降級用近3個月 vs SPY 估算
    狀態C - 次新股（<252天）：降級用近3個月 vs SPY 估算
    所有狀態都返回可比較的 raw score，直接進入全市場百分位排名
    """
    try:
        n = len(stock_close)
        # 狀態C：次新股，不足252天
        if n < 252:
            if spy_close is not None and n >= 63:
                recent_s   = stock_close.iloc[-1] / stock_close.iloc[-63] - 1
                recent_spy = spy_close.iloc[-1]   / spy_close.iloc[-63]   - 1
                return recent_s - recent_spy
            return 0.0
        recent = stock_close.iloc[-1]  / stock_close.iloc[-63]  - 1
        old    = stock_close.iloc[-63] / stock_close.iloc[-252] - 1
        # 狀態B：數據中毒（分拆/合併未調整），降級估算
        if abs(recent) > 3.0 or abs(old) > 3.0:
            if spy_close is not None and len(spy_close) >= 63:
                recent_spy = spy_close.iloc[-1] / spy_close.iloc[-63] - 1
                r = max(min(recent, 3.0), -1.0)
                return r - recent_spy
            return 0.0
        # 狀態A：正常股，完整公式
        return recent * 0.40 + old * 0.60
    except Exception:
        return 0.0


def normalize_rs_ratings(candidates: list) -> None:
    """第二階段：全市場百分位排名（IBD 標準）"""
    scores = []
    for c in candidates:
        raw = c.get("tech", {}).get("details", {}).get("rs_raw_score", 0) or 0
        scores.append((c, raw))
    if not scores:
        return
    scores_sorted = sorted(scores, key=lambda x: x[1])
    total = len(scores_sorted)
    for rank, (c, _) in enumerate(scores_sorted, 1):
        pct = max(1, min(99, round(rank / total * 99)))
        if c.get("tech", {}).get("details") is not None:
            c["tech"]["details"]["rs_rating"] = pct


def calc_rs_rating(stock_close, spy_close=None) -> float:
    """RS Rating 估算版（單股查詢用）"""
    try:
        if len(stock_close) < 252:
            return 50.0
        stock_score = calc_rs_score(stock_close, spy_close)
        if spy_close is not None and len(spy_close) >= 252:
            spy_score = calc_rs_score(spy_close)
        else:
            spy_score = 0.0
        relative = stock_score - spy_score
        rating = 50 + (relative / 0.30) * 50
        return round(max(1, min(99, rating)), 1)
    except Exception:
        return 50.0


def calc_volume_dryness(df: pd.DataFrame, lookback: int = 5, baseline: int = 20) -> float:
    """
    突破前成交量乾燥度
    最近 N 天成交量 vs 20 天均量的比值
    比值越低越好，< 0.6 是理想狀態
    """
    if len(df) < baseline:
        return 1.0
    recent_avg  = df["volume"].tail(lookback).mean()
    baseline_avg = df["volume"].tail(baseline).mean()
    if baseline_avg == 0:
        return 1.0
    return round(recent_avg / baseline_avg, 2)


def calc_volume_ratio(df: pd.DataFrame, short: int = 20, long: int = 60) -> float:
    if len(df) < long:
        return 1.0
    short_avg = df["volume"].tail(short).mean()
    long_avg  = df["volume"].tail(long).mean()
    return round(short_avg / long_avg, 2) if long_avg > 0 else 1.0


def calc_updown_volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    recent = df.tail(period).copy()
    recent["chg"] = recent["close"].diff()
    up_vol   = recent.loc[recent["chg"] > 0, "volume"].mean()
    down_vol = recent.loc[recent["chg"] < 0, "volume"].mean()
    if down_vol == 0 or np.isnan(down_vol):
        return 2.0
    return round(up_vol / down_vol, 2)


def calc_weekly_ema_alignment(df: pd.DataFrame) -> dict:
    """
    把日線數據重採樣成週線，判斷週線 EMA 排列
    """
    try:
        weekly = df["close"].resample("W").last().dropna()
        if len(weekly) < 40:
            return {"aligned": False, "ema10w": None, "ema20w": None, "ema40w": None}

        ema10w = weekly.ewm(span=10, adjust=False).mean()
        ema20w = weekly.ewm(span=20, adjust=False).mean()
        ema40w = weekly.ewm(span=40, adjust=False).mean()

        price  = weekly.iloc[-1]
        e10    = ema10w.iloc[-1]
        e20    = ema20w.iloc[-1]
        e40    = ema40w.iloc[-1]

        aligned = price > e10 > e20 > e40

        return {
            "aligned": aligned,
            "ema10w":  round(e10, 2),
            "ema20w":  round(e20, 2),
            "ema40w":  round(e40, 2),
            "price":   round(price, 2),
        }
    except Exception:
        return {"aligned": False, "ema10w": None, "ema20w": None, "ema40w": None}


def is_sma200_rising(df: pd.DataFrame, lookback: int = 20) -> bool:
    sma200 = calc_sma(df["close"], 200)
    if len(sma200.dropna()) < lookback:
        return False
    return sma200.iloc[-1] > sma200.iloc[-lookback]


def add_all_indicators(df: pd.DataFrame, spy_df: pd.DataFrame = None) -> pd.DataFrame:
    df = df.copy()
    df["ema20"]  = calc_ema(df["close"], 20)
    df["ema50"]  = calc_ema(df["close"], 50)
    df["ema150"] = calc_ema(df["close"], 150)
    df["ema200"] = calc_ema(df["close"], 200)
    df["sma200"] = calc_sma(df["close"], 200)
    df["atr14"]  = calc_atr(df, 14)
    df["vol_sma20"] = calc_sma(df["volume"], 20)
    df["vol_sma50"] = calc_sma(df["volume"], 50)
    df["52w_high"]  = df["high"].rolling(252).max()
    df["52w_low"]   = df["low"].rolling(252).min()
    if spy_df is not None:
        df["rs_line"] = calc_rs_line(df["close"], spy_df["close"])
    return df


def calc_institutional_sweep(df: pd.DataFrame, lookback: int = 60,
                               vol_multiplier: float = 1.2,
                               min_gain: float = 0.01) -> dict:
    """
    檢測過去 lookback 天內是否有機構掃貨痕跡
    條件（三合一）：
      1. 當天成交量 > 20日均量 × vol_multiplier
      2. 當天漲幅 > min_gain
      3. 當天收盤價 > EMA50（中期趨勢沒壞，允許在EMA20附近盤整的VCP）
    返回：{"detected": bool, "best_gain": float, "best_date": date}
    """
    if df is None or len(df) < max(lookback, 50):
        return {"detected": False, "best_gain": 0, "best_date": None}

    df = df.copy()
    if "ema20" not in df.columns:
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    if "ema50" not in df.columns:
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    recent = df.tail(lookback).copy()
    vol_ma20 = df["volume"].rolling(20).mean().reindex(recent.index)

    best_gain = 0
    best_date = None

    for idx in recent.index:
        row      = recent.loc[idx]
        vol_avg  = vol_ma20.loc[idx]
        if vol_avg is None or vol_avg == 0:
            continue

        close    = row["close"]
        open_    = row.get("open", close)
        ema50    = row.get("ema50", 0)
        volume   = row["volume"]
        day_gain = (close - open_) / open_ if open_ > 0 else 0

        if (volume > vol_avg * vol_multiplier      # 放量
                and day_gain > min_gain             # 當天漲幅 > 1%
                and ema50 > 0 and close > ema50):  # 收盤在 EMA50 上方
            if day_gain > best_gain:
                best_gain = day_gain
                best_date = idx

    detected = best_date is not None
    return {
        "detected":   detected,
        "best_gain":  round(float(best_gain) * 100, 1),
        "best_date":  str(best_date)[:10] if best_date is not None else None,
    }


def calc_pullback_score(df: pd.DataFrame, spy_rs: float = 50,
                         regime: str = "normal") -> dict:
    """
    回測買入策略的綜合排序分
    排序分 = 距離分 × 0.5 + 量乾燥度分 × 0.3 + RS評分 × 0.2
    B 軌（EMA50 回測）再 × 0.9

    regime: "normal" | "bull_hot"
    bull_hot 時 VDU 閾值放寬：A 軌 80%，B 軌 65%
    """
    if df is None or len(df) < 60:
        return {"score": 0, "track": None, "dist": 999, "vdr": 999}

    # 確保指標已計算
    if "ema20" not in df.columns:
        df = add_all_indicators(df)

    last   = df.iloc[-1]
    price  = float(last["close"])
    ema20  = float(last.get("ema20", 0) or 0)
    ema50  = float(last.get("ema50", 0) or 0)
    vdr    = calc_volume_dryness(df)

    if ema20 <= 0 or ema50 <= 0:
        return {"score": 0, "track": None, "dist": round((price - ema20) / ema20 * 100, 2) if ema20 > 0 else 999, "vdr": float(vdr)}

    dist20 = (price - ema20) / ema20 * 100
    dist50 = (price - ema50) / ema50 * 100

    # 判斷 A 軌或 B 軌
    track = None
    dist  = 999

    # 動態 VDU 閾值（BULL_HOT 市場放寬，因為全市場量能通膨）
    is_bull_hot   = regime == "bull_hot"
    vdu_limit_a   = 0.80 if is_bull_hot else 0.65
    vdu_limit_b   = 0.65 if is_bull_hot else 0.50

    # A 軌：距 EMA20 在 -1% ~ +2%
    if -1 <= dist20 <= 2 and vdr <= vdu_limit_a:
        track = "A"
        dist  = abs(dist20)

    # B 軌：距 EMA50 在 -0.5% ~ +1.5%，EMA20 > EMA50（無死亡交叉）
    elif -0.5 <= dist50 <= 1.5 and vdr <= vdu_limit_b and ema20 > ema50:
        track = "B"
        dist  = abs(dist50)

    # 不管是否符合軌道，都返回真實數值
    if track is None:
        reasons = []
        if dist20 > 2:   reasons.append(f'距EMA20 {dist20:+.1f}% > 2%')
        if dist20 < -1:  reasons.append(f'距EMA20 {dist20:+.1f}% < -1%')
        if vdr > 0.65:   reasons.append(f'量乾燥度 {vdr*100:.0f}% > 65%')
        return {
            "score":   0,
            "track":   None,
            "dist":    round(float(dist20), 2),
            "dist50":  round(float(dist50), 2),
            "vdr":     round(float(vdr), 3),
            "reasons": reasons,
        }

    # 計算各項分數（0-1 範圍）
    # 距離分：dist 越小越好，最大 3%
    dist_score = max(0, 1 - dist / 3)

    # 量乾燥度分：vdr 越小越好，最大 1.0
    vdr_score  = max(0, 1 - vdr)

    # RS 評分分：0-100 標準化
    rs_score   = min(spy_rs / 100, 1.0)

    # 複合排序分
    score = dist_score * 0.5 + vdr_score * 0.3 + rs_score * 0.2

    # B 軌修正係數
    if track == "B":
        score *= 0.9

    # 策略標記
    if is_bull_hot:
        strategy = "⚡ 牛市寬鬆回測"
    else:
        strategy = "📍 回測買入"

    return {
        "score":    round(score, 4),
        "track":    track,
        "dist":     round(dist20, 2),
        "dist50":   round(dist50, 2),
        "vdr":      round(vdr, 3),
        "regime":   regime,
        "strategy": strategy,
    }


def calc_sector_stats(candidates: list) -> dict:
    """
    第二階段：全市場掃描完後，計算板塊統計數據
    輸入：candidates = _analyze_one 的結果列表
    輸出：sector_stats 字典，key = sector 名稱
    """
    from collections import defaultdict
    import statistics

    sector_data = defaultdict(lambda: {
        "rs_list": [],
        "eps_raw_list": [],
        "tickers": [],
    })

    # 收集每隻股票的板塊數據
    for c in candidates:
        sector = c.get("sector", "Unknown") or "Unknown"
        rs     = c.get("tech", {}).get("details", {}).get("rs_rating", 0) or 0
        fund   = c.get("fund", {})
        eps    = fund.get("eps_quarters", [])

        sector_data[sector]["rs_list"].append(rs)
        sector_data[sector]["eps_raw_list"].append(eps)
        sector_data[sector]["tickers"].append(c.get("ticker", ""))

    # 計算每個板塊的統計數據
    sector_stats = {}
    for sector, data in sector_data.items():
        rs_list  = data["rs_list"]
        # qoq_list 已棄用，改用總利潤加總法
        total    = len(rs_list)

        rs_median    = statistics.median(rs_list) if rs_list else 0
        hot_count    = sum(1 for r in rs_list if r >= 80)
        # 總利潤加總法（控股集團合併報表）
        # NVDA/MSFT 的百億利潤自然壓過 MRVL 的小虧損
        eps_raw_list = data["eps_raw_list"]
        total_this_q = sum(e[0] for e in eps_raw_list if len(e) >= 2)
        total_last_q = sum(e[1] for e in eps_raw_list if len(e) >= 2)
        eps_positive = total_this_q >= total_last_q * 0.95 if total_last_q != 0 else True

        sector_stats[sector] = {
            "rs_median":    round(rs_median, 1),
            "total":        total,
            "hot_count":    hot_count,           # RS > 80 的隻數
            "eps_positive": eps_positive,        # 板塊 EPS 趨勢是否正向
            "tickers":      data["tickers"],
            "eps_raw_list": data["eps_raw_list"],
        }

    # 按 RS 中位數排名
    sorted_sectors = sorted(
        sector_stats.items(),
        key=lambda x: x[1]["rs_median"],
        reverse=True
    )
    total_sectors = len(sorted_sectors)
    for rank, (sector, stats) in enumerate(sorted_sectors, 1):
        stats["rank"]          = rank
        stats["total_sectors"] = total_sectors

    # 雙向雷達：YoY + QoQ 四狀態判定
    def calc_eps_state(stats):
        eps_raw = stats.get("eps_raw_list", [])
        this_q  = sum(e[0] for e in eps_raw if len(e) >= 2)
        last_q  = sum(e[1] for e in eps_raw if len(e) >= 2)
        yoy_q   = sum(e[4] for e in eps_raw if len(e) >= 5)

        qoq = (this_q - last_q) / abs(last_q) if last_q != 0 else 0
        yoy = (this_q - yoy_q)  / abs(yoy_q)  if yoy_q  != 0 else 0

        if yoy > 0 and qoq > 0:
            state      = "davis_double"
            label      = "🟢 戴維斯雙擊"
            multiplier = 1.15
        elif yoy > 0 and qoq >= -0.15:
            state      = "seasonal"
            label      = "✅ 季節性波動"
            multiplier = 1.00
        elif yoy < 0 and qoq > 0.50:
            state      = "turnaround"
            label      = "📈 谷底大復甦"
            multiplier = 1.10
        else:
            state      = "fake_breakout"
            label      = "💥 偽強勢假突破"
            multiplier = 0.80

        return {
            "qoq":        round(max(-999.0, min(999.0, qoq * 100)), 1),
            "yoy":        round(max(-999.0, min(999.0, yoy * 100)), 1),
            "state":      state,
            "label":      label,
            "multiplier": multiplier,
        }

    sorted_by_eps = sorted(
        sector_stats.items(),
        key=lambda x: calc_eps_state(x[1])["yoy"],
        reverse=True
    )
    for eps_rank, (sector, stats) in enumerate(sorted_by_eps, 1):
        eps_state = calc_eps_state(stats)
        stats["eps_rank"]      = eps_rank
        stats["eps_qoq_pct"]   = eps_state["qoq"]
        stats["eps_yoy_pct"]   = eps_state["yoy"]
        stats["eps_state"]     = eps_state["state"]
        stats["eps_label"]     = eps_state["label"]
        stats["eps_multiplier"] = eps_state["multiplier"]

        # 熱度標籤
        # 熱度標籤按 RS 中位數絕對值，不受板塊數量影響
        rs_med = stats["rs_median"]
        if rs_med >= 80:
            stats["label"] = "🔥 極熱"
        elif rs_med >= 65:
            stats["label"] = "♨️ 熱門"
        elif rs_med >= 50:
            stats["label"] = "🌤️ 普通"
        else:
            stats["label"] = "❄️ 冷淡"

    return sector_stats


def calc_sector_score(ticker: str, sector: str, rs_rating: float,
                      sector_stats: dict) -> dict:
    """
    計算個股的板塊強度分（0-1）
    同時返回板塊資訊供卡片顯示用
    """
    if not sector or sector not in sector_stats:
        return {
            "score":          0,
            "rank_str":       "N/A",
            "label":          "❓ 未知",
            "hot_str":        "N/A",
            "stock_rank_str": "N/A",
            "rs_median":      0,
        }

    stats = sector_stats[sector]
    rank           = stats["rank"]
    total_sectors  = stats["total_sectors"]
    rs_median      = stats["rs_median"]
    hot_count      = stats["hot_count"]
    total_stocks   = stats["total"]
    eps_positive   = stats["eps_positive"]
    label          = stats["label"]

    # 板塊 RS 中位數分（0-1）
    rs_score = min(rs_median / 100, 1.0)

    # 好兄弟密度分（0-1）
    hot_density = hot_count / total_stocks if total_stocks > 0 else 0

    # EPS 趨勢分
    eps_score = 1.0 if eps_positive else 0.0

    # 板塊強度分
    sector_score = rs_score * 0.5 + hot_density * 0.3 + eps_score * 0.2

    # 個股在板塊內的排名（依 RS Rating，由 orchestrator 計算後填入 tickers_with_rs）
    stock_rank = 0
    for i, t in enumerate(stats.get("tickers_with_rs", []), 1):
        if t[0] == ticker:
            stock_rank = i
            break

    eps_rank       = stats.get("eps_rank", 0)
    eps_qoq_pct    = stats.get("eps_qoq_pct", 0)
    eps_yoy_pct    = stats.get("eps_yoy_pct", 0)
    eps_label      = stats.get("eps_label", "")
    eps_state      = stats.get("eps_state", "fake_breakout")
    eps_multiplier = stats.get("eps_multiplier", 1.0)

    return {
        "score":           round(sector_score, 4),
        "rank_str":        f"{rank}/{total_sectors}",
        "label":           label,
        "eps_rank_str":    f"{eps_rank}/{total_sectors}",
        "eps_label":       eps_label,
        "eps_state":       eps_state,
        "eps_multiplier":  eps_multiplier,
        "eps_qoq_pct":     eps_qoq_pct,
        "eps_yoy_pct":     eps_yoy_pct,
        "hot_str":         f"{hot_count}/{total_stocks} 隻",
        "rs_median":       rs_median,
        "eps_positive":    eps_positive,
        "stock_rank_str":  f"{stock_rank}/{total_stocks}" if stock_rank else "N/A",
    }
