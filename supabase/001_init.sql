-- 투자 대시보드 초기 스키마 (단일 사용자, 앱 서버만 secret key로 접근)

create table assets (
  symbol text primary key,              -- 예: 005930.KS, AAPL
  name text not null,
  market text,                          -- KRX, KOSDAQ, US ...
  currency text not null default 'KRW',
  asset_class text,                     -- 주식, 채권, 금, 현금, 코인 ...
  created_at timestamptz not null default now()
);

create table indicator_settings (
  id bigint generated always as identity primary key,
  symbol text not null,                 -- yfinance 티커 또는 ecos:<코드>
  source text not null default 'yfinance', -- yfinance | ecos | fred
  display_name text not null,
  group_name text,
  sort_order int not null default 0,
  is_visible boolean not null default true
);

create table watchlist (
  id bigint generated always as identity primary key,
  symbol text not null references assets(symbol),
  group_name text,
  memo text,
  created_at timestamptz not null default now(),
  unique (symbol, group_name)
);

create table chart_drawings (
  id bigint generated always as identity primary key,
  symbol text not null,
  interval text not null default '1d',  -- 30m, 1d, 1wk, 1mo
  kind text not null,                   -- trendline | text | measure
  points jsonb not null,                -- [{"t": "...", "p": 123.4}, ...]
  text text,
  created_at timestamptz not null default now()
);

create table accounts (
  id bigint generated always as identity primary key,
  name text not null unique,            -- ISA, 연금, 해외주식 ...
  broker text,
  currency text not null default 'KRW'
);

create table transactions (
  id bigint generated always as identity primary key,
  trade_date date not null,
  account_id bigint references accounts(id),
  symbol text not null references assets(symbol),
  side text not null check (side in ('buy','sell','dividend')),
  quantity numeric not null default 0,
  price numeric not null default 0,
  currency text not null,
  fee numeric not null default 0,
  memo text,
  created_at timestamptz not null default now()
);

create view holdings with (security_invoker = true) as
select account_id, symbol,
  sum(case side when 'buy' then quantity when 'sell' then -quantity else 0 end) as quantity,
  sum(case when side = 'buy' then quantity * price + fee else 0 end)
    / nullif(sum(case when side = 'buy' then quantity else 0 end), 0) as avg_cost
from transactions
group by account_id, symbol;

create table target_allocation (
  asset_class text primary key,
  target_weight numeric not null check (target_weight between 0 and 100)
);

create table backtest_presets (
  id bigint generated always as identity primary key,
  name text not null,
  allocations jsonb not null,           -- {"US_STOCK": 60, "US_LTB": 30, "GOLD": 10}
  start_date date,
  end_date date,
  rebalance text not null default 'yearly', -- none | quarterly | yearly
  initial_amount numeric not null default 10000000,
  monthly_contribution numeric not null default 0,
  benchmark text default '^GSPC',
  created_at timestamptz not null default now()
);

create table quotes (
  id bigint generated always as identity primary key,
  guru text not null,
  quote_ko text not null,
  quote_original text,
  source text,
  theme text
);

create table daily_quotes (
  date date not null,
  quote_id bigint not null references quotes(id),
  meaning text,
  my_portfolio_note text,
  primary key (date, quote_id)
);

create table youtube_channels (
  channel_id text primary key,
  channel_name text not null,
  is_active boolean not null default true
);

create table videos (
  video_id text primary key,
  channel_id text not null references youtube_channels(channel_id),
  title text not null,
  description text,
  published_at timestamptz,
  thumbnail_url text
);

create table daily_videos (
  date date not null,
  video_id text not null references videos(video_id),
  rank int not null,
  reason text,
  watched boolean not null default false,
  liked boolean not null default false,
  primary key (date, video_id)
);

-- 모든 테이블 RLS 켜기 (정책 없음 = 공개 키로는 접근 불가, secret key만 접근)
alter table assets enable row level security;
alter table indicator_settings enable row level security;
alter table watchlist enable row level security;
alter table chart_drawings enable row level security;
alter table accounts enable row level security;
alter table transactions enable row level security;
alter table target_allocation enable row level security;
alter table backtest_presets enable row level security;
alter table quotes enable row level security;
alter table daily_quotes enable row level security;
alter table youtube_channels enable row level security;
alter table videos enable row level security;
alter table daily_videos enable row level security;

-- 기본 지표
insert into indicator_settings (symbol, source, display_name, group_name, sort_order) values
 ('^GSPC','yfinance','S&P 500','지수',1),
 ('^NDX','yfinance','나스닥 100','지수',2),
 ('^KS11','yfinance','코스피','지수',3),
 ('^KQ11','yfinance','코스닥','지수',4),
 ('KRW=X','yfinance','원/달러','환율',5),
 ('^TNX','yfinance','미국 10년물','금리',6),
 ('ecos:KR10Y','ecos','한국 10년물','금리',7),
 ('ecos:CD91','ecos','CD 91일','금리',8),
 ('^VIX','yfinance','VIX','변동성',9),
 ('CL=F','yfinance','WTI','원자재',10),
 ('GC=F','yfinance','금','원자재',11),
 ('BTC-USD','yfinance','비트코인','코인',12);
