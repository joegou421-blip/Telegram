-- 在 Supabase SQL Editor 執行這個檔案

-- 觀察清單
create table if not exists watchlist (
  id           bigserial primary key,
  ticker       text not null,
  short_name   text,
  sector       text,
  price_added  numeric,
  tech_score   integer,
  fund_score   integer,
  composite    integer,
  stop_loss    numeric,
  added_at     timestamptz default now(),
  removed_at   timestamptz,
  status       text default 'watching',  -- watching / removed / entered
  notes        text
);

create unique index if not exists watchlist_ticker_watching
  on watchlist(ticker) where status = 'watching';

-- 掃描歷史
create table if not exists scan_history (
  id             bigserial primary key,
  scan_date      date not null,
  ticker         text not null,
  tech_score     integer,
  fund_score     integer,
  composite      integer,
  price          numeric,
  market_gate    text,
  pattern_type   text,
  stop_loss      numeric,
  breakout_point numeric,
  created_at     timestamptz default now()
);

create index if not exists scan_history_ticker_date
  on scan_history(ticker, scan_date desc);

-- Row Level Security（讓 API 可以讀寫）
alter table watchlist    enable row level security;
alter table scan_history enable row level security;

create policy "allow all" on watchlist    for all using (true);
create policy "allow all" on scan_history for all using (true);
