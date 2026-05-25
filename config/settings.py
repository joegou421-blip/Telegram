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
    "rs_rating":        15,   # RS Rating（跑贏多少股票）
    "weekly_trend":     10,   # 週線確認
    "volume_struct":    20,   # 成交量結構
    "volume_dryness":   10,   # 突破前成交量乾燥度
    "pattern":          20,   # VCP / Cup & Handle
    "atr_risk":          5,   # ATR 止損合理性
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
