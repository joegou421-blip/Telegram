// cloudflare-worker/index.js
// 部署到 Cloudflare Workers，處理 Telegram Webhook 雙向互動

const TELEGRAM_TOKEN  = typeof TELEGRAM_BOT_TOKEN !== 'undefined' ? TELEGRAM_BOT_TOKEN : '';
const OPENROUTER_KEY  = typeof OPENROUTER_API_KEY  !== 'undefined' ? OPENROUTER_API_KEY  : '';
const SUPABASE_URL_CF = typeof SUPABASE_URL         !== 'undefined' ? SUPABASE_URL         : '';
const SUPABASE_KEY_CF = typeof SUPABASE_KEY         !== 'undefined' ? SUPABASE_KEY         : '';

const TG_API  = `https://api.telegram.org/bot${TELEGRAM_TOKEN}`;
const AI_MODEL = 'anthropic/claude-sonnet-4-5';

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('OK', { status: 200 });
    }

    const update = await request.json();

    // 處理按鈕點擊（callback query）
    if (update.callback_query) {
      await handleCallback(update.callback_query, env);
      return new Response('OK');
    }

    // 處理文字訊息
    if (update.message?.text) {
      await handleMessage(update.message, env);
      return new Response('OK');
    }

    return new Response('OK');
  }
};

// ── 處理文字訊息 ──────────────────────────────────────────

async function handleMessage(msg, env) {
  const chatId = msg.chat.id.toString();
  const text   = msg.text.trim();

  // 分析個股：「分析 SNDK」或直接「SNDK」
  const tickerMatch = text.match(/^(?:分析\s*)?([A-Z]{1,5})$/i);
  if (tickerMatch) {
    const ticker = tickerMatch[1].toUpperCase();
    await sendTyping(chatId);
    await analyzeStock(chatId, ticker, env);
    return;
  }

  // 觀察清單查詢
  if (text.includes('觀察清單')) {
    await sendTyping(chatId);
    await sendWatchlistSummary(chatId, env);
    return;
  }

  // 其他問題：交給 AI 自由回答
  await sendTyping(chatId);
  const reply = await askAI(text, env);
  await sendMessage(chatId, reply);
}

// ── 處理按鈕 ─────────────────────────────────────────────

async function handleCallback(cb, env) {
  const chatId = cb.message.chat.id.toString();
  const data   = cb.data;
  const cbId   = cb.id;

  if (data.startsWith('add_')) {
    const ticker = data.replace('add_', '');
    const ok = await addToWatchlist(ticker, chatId, env);
    await answerCallback(cbId, ok ? `${ticker} 已加入觀察清單` : '加入失敗，請稍後再試');
    if (ok) {
      // 更新訊息按鈕（加入後換成移除按鈕）
      await editMessageKeyboard(chatId, cb.message.message_id, ticker, true);
    }
    return;
  }

  if (data.startsWith('remove_')) {
    const ticker = data.replace('remove_', '');
    const ok = await removeFromWatchlist(ticker, env);
    await answerCallback(cbId, ok ? `${ticker} 已移除觀察清單` : '移除失敗');
    if (ok) {
      await editMessageKeyboard(chatId, cb.message.message_id, ticker, false);
    }
    return;
  }

  if (data.startsWith('deep_')) {
    const ticker = data.replace('deep_', '');
    await answerCallback(cbId, `正在深度分析 ${ticker}...`);
    await sendTyping(chatId);
    const analysis = await deepAnalysis(ticker, env);
    await sendMessage(chatId, analysis);
    return;
  }
}

// ── 分析個股 ─────────────────────────────────────────────

async function analyzeStock(chatId, ticker, env) {
  // 抓數據 + 檢查觀察清單
  const [stockData, watchlistEntry] = await Promise.all([
    fetchStockData(ticker, env),
    checkWatchlist(ticker, env),
  ]);

  if (!stockData) {
    await sendMessage(chatId, `找不到 ${ticker} 的數據，請確認股票代碼是否正確。`);
    return;
  }

  const msg      = formatStockCard(ticker, stockData, watchlistEntry);
  const keyboard = buildKeyboard(ticker, !!watchlistEntry);

  await sendMessage(chatId, msg, keyboard);
}

// ── 觀察清單操作 ─────────────────────────────────────────

async function sendWatchlistSummary(chatId, env) {
  const list = await getWatchlist(env);
  if (!list || list.length === 0) {
    await sendMessage(chatId, '你的觀察清單目前是空的。\n\n直接輸入股票代碼（例如 SNDK）可以分析並加入觀察清單。');
    return;
  }

  let msg = `👁 觀察清單（${list.length} 隻）\n${'─'.repeat(20)}\n`;
  for (const item of list) {
    const addedDate  = item.added_at?.slice(0, 10) || 'N/A';
    const priceAdded = item.price_added || 'N/A';
    msg += `\n📊 ${item.ticker}  ${item.short_name || ''}\n`;
    msg += `   加入：${addedDate}  當時價格：$${priceAdded}\n`;
    msg += `   評分：技術 ${item.tech_score} · 基本面 ${item.fund_score}\n`;
    msg += `   止損：$${item.stop_loss || 'N/A'}\n`;
  }
  msg += `\n回覆「分析觀察清單」可重新評分所有股票。`;
  await sendMessage(chatId, msg);
}

async function addToWatchlist(ticker, chatId, env) {
  try {
    const res = await fetch(`${SUPABASE_URL_CF}/rest/v1/watchlist`, {
      method:  'POST',
      headers: {
        'apikey':        SUPABASE_KEY_CF,
        'Authorization': `Bearer ${SUPABASE_KEY_CF}`,
        'Content-Type':  'application/json',
        'Prefer':        'return=minimal',
      },
      body: JSON.stringify({
        ticker,
        added_at: new Date().toISOString(),
        status:   'watching',
      }),
    });
    return res.ok;
  } catch (e) {
    console.error('addToWatchlist error:', e);
    return false;
  }
}

async function removeFromWatchlist(ticker, env) {
  try {
    const res = await fetch(
      `${SUPABASE_URL_CF}/rest/v1/watchlist?ticker=eq.${ticker}&status=eq.watching`,
      {
        method:  'PATCH',
        headers: {
          'apikey':        SUPABASE_KEY_CF,
          'Authorization': `Bearer ${SUPABASE_KEY_CF}`,
          'Content-Type':  'application/json',
        },
        body: JSON.stringify({ status: 'removed', removed_at: new Date().toISOString() }),
      }
    );
    return res.ok;
  } catch (e) {
    return false;
  }
}

async function checkWatchlist(ticker, env) {
  try {
    const res = await fetch(
      `${SUPABASE_URL_CF}/rest/v1/watchlist?ticker=eq.${ticker}&status=eq.watching&select=*`,
      {
        headers: {
          'apikey':        SUPABASE_KEY_CF,
          'Authorization': `Bearer ${SUPABASE_KEY_CF}`,
        },
      }
    );
    const data = await res.json();
    return data?.[0] || null;
  } catch (e) {
    return null;
  }
}

async function getWatchlist(env) {
  try {
    const res = await fetch(
      `${SUPABASE_URL_CF}/rest/v1/watchlist?status=eq.watching&select=*&order=added_at.desc`,
      {
        headers: {
          'apikey':        SUPABASE_KEY_CF,
          'Authorization': `Bearer ${SUPABASE_KEY_CF}`,
        },
      }
    );
    return await res.json();
  } catch (e) {
    return [];
  }
}

// ── 深度分析 ─────────────────────────────────────────────

async function deepAnalysis(ticker, env) {
  const prompt = `你是專業的 swing trader。請對 ${ticker} 做深度分析，包括：
1. 技術面：當前形態品質、EMA 排列、成交量行為
2. 基本面：EPS 增長動能、機構動向
3. 大盤環境對這隻股票的影響
4. 具體建議：現在是否適合進場？止損設在哪裡？
請用繁體中文，條列清晰，控制在 200 字內。`;

  return await askAI(prompt, env);
}

// ── AI 問答 ──────────────────────────────────────────────

async function askAI(prompt, env) {
  try {
    const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method:  'POST',
      headers: {
        'Authorization': `Bearer ${OPENROUTER_KEY}`,
        'Content-Type':  'application/json',
      },
      body: JSON.stringify({
        model:      AI_MODEL,
        max_tokens: 500,
        messages:   [{ role: 'user', content: prompt }],
      }),
    });
    const data = await res.json();
    return data.choices?.[0]?.message?.content || '分析失敗，請稍後再試。';
  } catch (e) {
    return '目前無法連接 AI，請稍後再試。';
  }
}

// ── 格式化卡片 ───────────────────────────────────────────

function formatStockCard(ticker, data, watchlistEntry) {
  const watchlistLine = watchlistEntry
    ? `👁 已在觀察清單（加入於 ${watchlistEntry.added_at?.slice(0,10)}，$${watchlistEntry.price_added}）\n`
    : '';

  return (
    `━━━━━━━━━━━━━━━━\n` +
    watchlistLine +
    `📊 ${ticker}\n` +
    `💰 $${data.price}  綜合評分：${data.composite_score}\n\n` +
    `📈 技術面 ${data.tech_score}/100\n` +
    `  EMA ${data.tech.breakdown.ema_alignment}/25  ` +
    `RS ${data.tech.breakdown.rs_line}/20  ` +
    `量 ${data.tech.breakdown.volume_struct}/20\n` +
    `  形態 ${data.tech.breakdown.pattern}/25  ` +
    `ATR ${data.tech.breakdown.atr_risk}/10\n\n` +
    `💼 基本面 ${data.fund_score}/100\n` +
    `  EPS ${data.fund.breakdown.eps_acceleration}/30  ` +
    `營收 ${data.fund.breakdown.revenue_margin}/25\n` +
    `  機構 ${data.fund.breakdown.institutional}/25  ` +
    `Sector ${data.fund.breakdown.sector_strength}/20\n\n` +
    `🎯 突破點 $${data.tech.details.breakout_point || 'N/A'}\n` +
    `🛑 止損 $${data.tech.details.stop_loss}（ATR ${data.tech.details.atr_risk_pct}%）`
  );
}

function buildKeyboard(ticker, inWatchlist) {
  return {
    inline_keyboard: [[
      { text: '深度分析', callback_data: `deep_${ticker}` },
      inWatchlist
        ? { text: '移除觀察清單', callback_data: `remove_${ticker}` }
        : { text: '加入觀察清單', callback_data: `add_${ticker}` },
    ]],
  };
}

// ── Telegram API 工具 ────────────────────────────────────

async function sendMessage(chatId, text, replyMarkup = null) {
  const body = { chat_id: chatId, text, parse_mode: 'HTML' };
  if (replyMarkup) body.reply_markup = replyMarkup;
  await fetch(`${TG_API}/sendMessage`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
}

async function sendTyping(chatId) {
  await fetch(`${TG_API}/sendChatAction`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ chat_id: chatId, action: 'typing' }),
  });
}

async function answerCallback(callbackQueryId, text) {
  await fetch(`${TG_API}/answerCallbackQuery`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ callback_query_id: callbackQueryId, text }),
  });
}

async function editMessageKeyboard(chatId, messageId, ticker, inWatchlist) {
  await fetch(`${TG_API}/editMessageReplyMarkup`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      chat_id:      chatId,
      message_id:   messageId,
      reply_markup: buildKeyboard(ticker, inWatchlist),
    }),
  });
}

async function fetchStockData(ticker, env) {
  // 這裡在真實環境中應該呼叫你的分析 API 或直接跑分析
  // 暫時回傳 null，讓 Worker 知道需要整合 Python 分析結果
  return null;
}
