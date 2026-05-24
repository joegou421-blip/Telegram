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

# 篩選條件
SCREENER = {
    "min_market_cap":       2_000_000_000,   # 2B
    "min_monthly_volume":   900_000_000,     # 900M USD
    "max_from_52w_high":    0.25,            # 距52週高 < 25%
    "min_price":            10.0,
}

# 評分門檻
SCORE_THRESHOLD = {
    "technical":   70,
    "fundamental": 60,
}

# 技術面評分權重
TECH_WEIGHTS = {
    "ema_alignment":  25,
    "rs_line":        20,
    "volume_struct":  20,
    "pattern":        25,
    "atr_risk":       10,
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
    "vix_red":          30,
    "vix_yellow":       20,
    "breadth_red":      0.40,
    "breadth_yellow":   0.60,
}

# AI 模型
AI_MODEL = "deepseek/deepseek-chat"

# ATR 止損倍數
ATR_MULTIPLIER   = 1.5
MAX_ATR_RISK_PCT = 0.08   # 8%

# Earnings 過濾天數
EARNINGS_BUFFER_DAYS = 7
