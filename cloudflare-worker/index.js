const AI_MODEL_FAST = 'deepseek/deepseek-v4-flash';
const AI_MODEL_DEEP = 'deepseek/deepseek-v4-pro';

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('OK', { status: 200 });
    }
    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response('Bad Request', { status: 400 });
    }
    try {
      if (update.callback_query) {
        await handleCallback(update.callback_query, env);
        return new Response('OK');
      }
      if (update.message?.text) {
        await handleMessage(update.message, env);
        return new Response('OK');
      }
    } catch (e) {
      console.error('頂層錯誤:', e.message);
    }
    return new Response('OK');
  }
};

// ── 處理訊息 ─────────────────────────────────────────────

async function handleMessage(msg, env) {
  const chatId = String(msg.chat.id);
  const text   = msg.text.trim();
  console.log('收到訊息:', text);

  if (text.startsWith('/')) {
    await sendMessage(chatId,
      '👋 歡迎使用 Swing Trade Agent！\n\n' +
      '你可以：\n' +
      '• 輸入股票代碼，例如：SNDK\n' +
      '• 說「分析 SNDK」或「分析一下 SNDK」\n' +
      '• 說「觀察清單」查看你的清單\n' +
      '• 問任何股市相關問題\n\n' +
      '個股分析約需 1-2 分鐘，結果會自動推送給你。',
      null, env
    );
    return;
  }

  // 識別股票代碼
  const tickerMatch = text.match(
    /(?:分析一下|分析|幫我分析|看看|分享一下)?[\s]*([A-Za-z]{1,5})[\s]*(?:的分析|怎樣|如何|好嗎|資料|信息)?$/
  );

  if (tickerMatch) {
    const ticker   = tickerMatch[1].toUpperCase();
    const excluded = ['HI', 'OK', 'NO', 'GO', 'IT', 'IS', 'BY', 'OR', 'AN'];
    if (!excluded.includes(ticker)) {
      await sendTyping(chatId, env);
      await sendMessage(chatId,
        `🔍 正在觸發 ${ticker} 的完整分析，約需 1-2 分鐘...\n結果會自動推送給你。`,
        null, env
      );
      await triggerGithubAnalysis(ticker, env);
      return;
    }
  }

  // 觀察清單
  if (text.includes('觀察清單')) {
    await sendTyping(chatId, env);
    await sendWatchlistSummary(chatId, env);
    return;
  }

  // 其他問題：AI 即時回答
  await sendTyping(chatId, env);
  const reply = await askAI(
    `你是專業的美股 swing trader 助手。用繁體中文簡潔回答：${text}`,
    AI_MODEL_FAST, env
  );
  await sendMessage(chatId, reply, null, env);
}

// ── 處理按鈕 ─────────────────────────────────────────────

async function handleCallback(cb, env) {
  const chatId = String(cb.message.chat.id);
  const data   = cb.data;
  const cbId   = cb.id;

  if (data.startsWith('add_')) {
    const ticker = data.replace('add_', '');
    const ok     = await addToWatchlist(ticker, env);
    await answerCallback(cbId, ok ? `✅ ${ticker} 已加入觀察清單` : '加入失敗', env);
    if (ok) await editKeyboard(chatId, cb.message.message_id, ticker, true, env);
    return;
  }
  if (data.startsWith('remove_')) {
    const ticker = data.replace('remove_', '');
    const ok     = await removeFromWatchlist(ticker, env);
    await answerCallback(cbId, ok ? `🗑 ${ticker} 已移除` : '移除失敗', env);
    if (ok) await editKeyboard(chatId, cb.message.message_id, ticker, false, env);
    return;
  }
  if (data.startsWith('deep_')) {
    const ticker = data.replace('deep_', '');
    await answerCallback(cbId, `正在深度分析 ${ticker}...`, env);
    await sendTyping(chatId, env);
    // 深度分析用 V4 Pro
    const analysis = await deepAnalysis(ticker, AI_MODEL_DEEP, env);
    await sendMessage(chatId, analysis, null, env);
    return;
  }
}

// ── 觸發 GitHub Actions 跑真實分析 ───────────────────────

async function triggerGithubAnalysis(ticker, env) {
  try {
    const url = `https://api.github.com/repos/${env.GITHUB_USERNAME}/${env.GITHUB_REPO}/actions/workflows/stock_scan.yml/dispatches`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
        'Accept':        'application/vnd.github.v3+json',
        'Content-Type':  'application/json',
      },
      body: JSON.stringify({
        ref:    'main',
        inputs: { scan_ticker: ticker },
      }),
    });
    console.log('GitHub Actions 觸發狀態:', res.status);
    if (!res.ok) {
      const err = await res.text();
      console.error('GitHub Actions 觸發失敗:', err);
    }
  } catch (e) {
    console.error('triggerGithubAnalysis:', e.message);
  }
}

// ── 觀察清單 ─────────────────────────────────────────────

async function sendWatchlistSummary(chatId, env) {
  const list = await getWatchlist(env);
  if (!list || list.length === 0) {
    await sendMessage(chatId, '你的觀察清單目前是空的。\n\n輸入股票代碼可以分析並加入。', null, env);
    return;
  }
  let msg = `👁 觀察清單（${list.length} 隻）\n${'─'.repeat(20)}\n`;
  for (const item of list) {
    const gain = item.price_added && item.current_price
      ? `  盈虧 ${((item.current_price - item.price_added) / item.price_added * 100).toFixed(1)}%`
      : '';
    msg += `\n📊 ${item.ticker}${item.short_name ? '  ' + item.short_name : ''}\n`;
    msg += `   加入：${item.added_at?.slice(0, 10) || 'N/A'}  價格：$${item.price_added || 'N/A'}${gain}\n`;
    msg += `   技術 ${item.tech_score ?? 'N/A'} · 基本面 ${item.fund_score ?? 'N/A'}\n`;
    if (item.stop_loss) msg += `   止損：$${item.stop_loss}\n`;
  }
  msg += '\n輸入股票代碼可重新觸發完整分析。';
  await sendMessage(chatId, msg, null, env);
}

async function addToWatchlist(ticker, env) {
  try {
    await fetch(`${env.SUPABASE_URL}/rest/v1/watchlist`, {
      method:  'POST',
      headers: {
        'apikey': env.SUPABASE_KEY, 'Authorization': `Bearer ${env.SUPABASE_KEY}`,
        'Content-Type': 'application/json', 'Prefer': 'return=minimal',
      },
      body: JSON.stringify({ ticker, added_at: new Date().toISOString(), status: 'watching' }),
    });
    return true;
  } catch (e) { return false; }
}

async function removeFromWatchlist(ticker, env) {
  try {
    await fetch(
      `${env.SUPABASE_URL}/rest/v1/watchlist?ticker=eq.${ticker}&status=eq.watching`,
      {
        method: 'PATCH',
        headers: {
          'apikey': env.SUPABASE_KEY, 'Authorization': `Bearer ${env.SUPABASE_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ status: 'removed', removed_at: new Date().toISOString() }),
      }
    );
    return true;
  } catch (e) { return false; }
}

async function checkWatchlist(ticker, env) {
  try {
    const res  = await fetch(
      `${env.SUPABASE_URL}/rest/v1/watchlist?ticker=eq.${ticker}&status=eq.watching&select=*`,
      { headers: { 'apikey': env.SUPABASE_KEY, 'Authorization': `Bearer ${env.SUPABASE_KEY}` } }
    );
    const data = await res.json();
    return data?.[0] || null;
  } catch (e) { return null; }
}

async function getWatchlist(env) {
  try {
    const res = await fetch(
      `${env.SUPABASE_URL}/rest/v1/watchlist?status=eq.watching&select=*&order=added_at.desc`,
      { headers: { 'apikey': env.SUPABASE_KEY, 'Authorization': `Bearer ${env.SUPABASE_KEY}` } }
    );
    return await res.json();
  } catch (e) { return []; }
}

// ── 深度分析 ─────────────────────────────────────────────

async function deepAnalysis(ticker, model, env) {
  return await askAI(
    `你是專業的美股 swing trader。請用繁體中文深度分析 ${ticker}：
1. 技術面：趨勢、EMA 排列、成交量、VCP 或 Cup & Handle 形態品質
2. 基本面：EPS 增長動能、板塊強弱、機構動向
3. 大盤環境的影響
4. 具體建議：現在適合進場嗎？突破點、止損點、預期目標
條列清晰，200 字內。`,
    model, env
  );
}

// ── AI 問答 ──────────────────────────────────────────────

async function askAI(prompt, model, env) {
  try {
    const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.OPENROUTER_API_KEY}`,
        'Content-Type':  'application/json',
        'HTTP-Referer':  'https://swing-trade-agent.github.io',
        'X-Title':       'Swing Trade Agent',
      },
      body: JSON.stringify({
        model,
        max_tokens: 600,
        messages:   [{ role: 'user', content: prompt }],
      }),
    });
    console.log('OpenRouter 狀態:', res.status);
    if (!res.ok) {
      const err = await res.text();
      console.error('OpenRouter 錯誤:', err);
      return '分析失敗，請稍後再試。';
    }
    const data = await res.json();
    return data.choices?.[0]?.message?.content?.trim() || '分析失敗，請稍後再試。';
  } catch (e) {
    console.error('askAI:', e.message);
    return '目前無法連接 AI，請稍後再試。';
  }
}

// ── Telegram 工具 ────────────────────────────────────────

async function sendMessage(chatId, text, replyMarkup, env) {
  const body = { chat_id: chatId, text, parse_mode: 'HTML' };
  if (replyMarkup) body.reply_markup = replyMarkup;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (e) { console.error('sendMessage:', e.message); }
}

async function sendTyping(chatId, env) {
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendChatAction`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, action: 'typing' }),
    });
  } catch (e) {}
}

async function answerCallback(cbId, text, env) {
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_query_id: cbId, text }),
    });
  } catch (e) {}
}

async function editKeyboard(chatId, messageId, ticker, inWatchlist, env) {
  const keyboard = {
    inline_keyboard: [[
      { text: '🔍 深度分析', callback_data: `deep_${ticker}` },
      inWatchlist
        ? { text: '🗑 移除觀察清單', callback_data: `remove_${ticker}` }
        : { text: '👁 加入觀察清單', callback_data: `add_${ticker}` },
    ]],
  };
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, message_id: messageId, reply_markup: keyboard }),
    });
  } catch (e) {}
}
