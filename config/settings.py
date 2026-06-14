import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
POLYGON_API_KEY    = os.getenv("POLYGON_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
SUPABASE_URL       = os.getenv("SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY")

# 單股掃描模式（Telegram 觸發時使用）
SCAN_TICKER = os.getenv("SCAN_TICKER", "").upper().strip()

# AI 模型
AI_MODEL_FAST  = "deepseek/deepseek-v4-flash"   # 日常分析
AI_MODEL_DEEP  = "deepseek/deepseek-v4-pro"     # 深度分析按鈕

# 篩選條件
SCREENER = {
    "min_market_cap":      2_000_000_000,
    "min_monthly_volume":  900_000_000,
    "max_from_52w_high":   0.25,
    "min_price":           10.0,
}

# 評分門檻
SCORE_THRESHOLD = {
    "technical":   70,
    "fundamental": 60,
}

# 技術面評分權重
TECH_WEIGHTS = {
    "ema_alignment":    20,   # 降低，騰出空間給新指標
    "rs_rating":        20,   # RS Rating（跑贏多少股票）；含原ATR的5分，RS是更核心的swing指標
    "weekly_trend":     10,   # 週線確認
    "volume_struct":    20,   # 成交量結構
    "volume_dryness":   10,   # 突破前成交量乾燥度
    "pattern":          20,   # VCP / Cup & Handle
    # ATR 止損僅供倉位/停損參考，不計入選股分數（中性股ATR常超過8%，納入會排除潛力股）
}

# 基本面評分權重
FUND_WEIGHTS = {
    "eps_acceleration": 30,
    "revenue_margin":   25,
    "institutional":    25,
    "sector_strength":  20,
}

# 大盤通行證門檻
MARKET_GATE = {
    "vix_red":        30,
    "vix_yellow":     20,
    "breadth_red":    0.40,
    "breadth_yellow": 0.60,
}

# ATR 止損設定
ATR_MULTIPLIER   = 1.5
MAX_ATR_RISK_PCT = 0.08

# Earnings 過濾天數
EARNINGS_BUFFER_DAYS = 7

# 止盈提醒閾值
TAKE_PROFIT_PCT = 0.20   # 漲 20% 提醒

# 連續出現天數門檻（黃燈時用）
CONSECUTIVE_DAYS_THRESHOLD = 3

# Follow-Through Day 設定
FTD_MIN_GAIN     = 0.017  # 最少上漲 1.7%
FTD_MIN_DAY      = 4      # 反彈第幾天起才算

# Distribution Days
DISTRIBUTION_DAYS_LOOKBACK   = 25
DISTRIBUTION_DAYS_THRESHOLD  = 6
DISTRIBUTION_DAY_DECLINE_PCT = 0.002   # -0.2%

# Classic / Momentum 排序權重
CLASSIC_RANKING_WEIGHTS  = {"leadership": 0.4, "timing": 0.6}
MOMENTUM_RANKING_WEIGHTS = {"leadership": 0.3, "pead": 0.7}
PEAD_THRESHOLD     = 70
PEAD_LOOKBACK_DAYS = 7

# EPS 雙向雷達（個股標籤，不計分，只解釋 Why）
EPS_RADAR_DAVIS_YOY_MIN   = 25   # 戴維斯雙擊：EPS YoY 門檻
EPS_RADAR_DAVIS_QOQ_MIN   = 10   # 戴維斯雙擊：EPS QoQ 門檻
EPS_RADAR_DAVIS_SECTOR_RANK_MAX = 3   # 戴維斯雙擊：板塊 RS 排名門檻（前N強）
EPS_RADAR_WEAK_SECTOR_RANK_MIN  = 20  # 偽強勢：板塊 RS 排名門檻（第N弱之後，視板塊分類粒度而定）
EPS_RADAR_WEAK_RS_MIN     = 90   # 偽強勢：個股 RS Rating 門檻
EPS_RADAR_WEAK_REV_YOY_MAX = 5   # 偽強勢：營收 YoY 上限
EPS_RADAR_WEAK_EPS_YOY_MAX = 10  # 偽強勢：EPS YoY 上限
WHY_MAX_ITEMS = 4   # Why Summary 最多顯示幾條

# Dashboard 顯示數量
DASHBOARD_TOP_N_CLASSIC  = 5
DASHBOARD_TOP_N_MOMENTUM = 3
