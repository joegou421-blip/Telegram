# Swing Trade Agent

AI 驅動的美股 Swing Trade 分析系統。

---

## 安裝步驟

### Step 1：申請所有 API Keys

| 服務 | 網址 | 用途 | 費用 |
|------|------|------|------|
| OpenRouter | openrouter.ai | AI 分析引擎 | ~$5/月 |
| Polygon.io | polygon.io | 股票數據主力 | 免費版 |
| Telegram Bot | @BotFather | 推送通知 | 免費 |
| Supabase | supabase.com | 數據庫 | 免費版 |

**申請步驟：**

1. **OpenRouter**：註冊後在 dashboard 建立 API Key，儲值 $10 夠用幾個月
2. **Polygon.io**：免費版每分鐘 5 次請求，夠用
3. **Telegram Bot**：
   - 在 Telegram 搜尋 @BotFather
   - 發送 /newbot，按指示建立
   - 記錄 Bot Token
   - 發訊息給你的 bot，然後訪問：
     https://api.telegram.org/bot你的TOKEN/getUpdates
   - 找到 "chat":{"id": 這個數字就是你的 Chat ID
4. **Supabase**：
   - 建立新 project
   - Settings → API 找到 URL 和 anon key

---

### Step 2：建立 GitHub Repository

1. 在 GitHub 建立新的 private repo
2. 把所有代碼 push 上去：

```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/你的帳號/swing-trade-agent.git
git push -u origin main
```

---

### Step 3：設定 GitHub Secrets

在 GitHub repo → Settings → Secrets and variables → Actions → New repository secret

逐一加入：
- `OPENROUTER_API_KEY`
- `POLYGON_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SUPABASE_URL`
- `SUPABASE_KEY`

---

### Step 4：建立 Supabase 數據表

1. 登入 Supabase → 你的 project → SQL Editor
2. 複製 `supabase_setup.sql` 的內容，貼上執行

---

### Step 5：部署 Cloudflare Worker（互動功能）

1. 註冊 Cloudflare 帳號（免費）
2. 安裝 Wrangler CLI：
```bash
npm install -g wrangler
wrangler login
```
3. 進入 worker 目錄：
```bash
cd cloudflare-worker
wrangler init
```
4. 設定環境變數：
```bash
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put OPENROUTER_API_KEY
wrangler secret put SUPABASE_URL
wrangler secret put SUPABASE_KEY
```
5. 部署：
```bash
wrangler deploy
```
6. 設定 Telegram Webhook（把 YOUR_WORKER_URL 換成你的 worker 網址）：
```
https://api.telegram.org/bot你的TOKEN/setWebhook?url=YOUR_WORKER_URL
```

---

### Step 6：測試

**本地測試（需要 .env 檔案）：**
```bash
cp .env.example .env
# 填入你的 keys
pip install -r requirements.txt
python main.py
```

**手動觸發 GitHub Actions：**
GitHub repo → Actions → Swing Trade Agent → Run workflow

---

## 日常使用

系統會自動在每天美東時間 6:30am 和 4:30pm 執行。

**在 Telegram 裡可以做的事：**
- 直接輸入股票代碼（例如 `SNDK`）：分析這隻股票
- 輸入「觀察清單」：查看你的觀察清單
- 點擊「加入觀察清單」：把股票加入追蹤
- 點擊「移除觀察清單」：移除
- 點擊「深度分析」：AI 深度分析

---

## 文件結構

```
swing-trade-agent/
├── .github/workflows/stock_scan.yml  # 定時排程
├── agents/
│   ├── technical_agent.py            # 技術面分析
│   ├── fundamental_agent.py          # 基本面分析
│   ├── market_agent.py               # 大盤環境
│   └── orchestrator.py               # 整合評分
├── data/
│   ├── fetcher.py                    # 多源數據抓取
│   └── indicators.py                 # 技術指標計算
├── screener/
│   └── universe.py                   # 第一層量化篩選
├── notifier/
│   └── telegram.py                   # Telegram 推送
├── watchlist/
│   └── database.py                   # Supabase 操作
├── cloudflare-worker/
│   └── index.js                      # 雙向互動 Worker
├── config/
│   └── settings.py                   # 所有設定
├── main.py                           # 主程式
├── supabase_setup.sql                # 建表 SQL
└── requirements.txt
```
