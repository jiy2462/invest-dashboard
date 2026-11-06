-- 3단계: 포트폴리오 (구글 시트 '투자 포트폴리오2026'의 전략·포트폴리오·종목별 탭 구조를 옮김)

-- 거래 당시 환율(원/외화). 원화 거래는 비워 두면 1로 본다. 원화 기준 평단·환차손익 계산용.
alter table transactions add column if not exists fx_rate numeric;
-- 배당(side='dividend')은 quantity 0, price = 받은 배당금 총액(거래 통화)으로 저장한다.

-- 시세가 잡히지 않는 종목(KRX 금현물 등)은 직접 입력한 현재가를 쓴다.
alter table assets add column if not exists manual_price numeric;
alter table assets add column if not exists manual_price_at timestamptz;

-- 자산군(시트 '전략'·'포트폴리오' 탭의 행): 목표 비중, 월 적립 목표, 목표 수익률, 현황·전략 메모
alter table target_allocation add column if not exists monthly_contribution numeric not null default 0;
alter table target_allocation add column if not exists target_return numeric;   -- %
alter table target_allocation add column if not exists status_note text;        -- 현황
alter table target_allocation add column if not exists strategy text;           -- 트레이딩 전략
alter table target_allocation add column if not exists sort_order int not null default 0;

-- 티커가 없는 현금성 자산(투자유보현금, RP, 적금 등)은 잔액만 기록한다.
create table if not exists cash_balances (
  id bigint generated always as identity primary key,
  name text not null,
  asset_class text not null,
  account_id bigint references accounts(id) on delete set null,
  currency text not null default 'KRW',
  amount numeric not null default 0,
  memo text,
  updated_at timestamptz not null default now()
);

-- 월말 기록 (시트 '전략' 탭의 2026.1 ~ 2026.12 열)
create table if not exists portfolio_snapshots (
  month date not null,                  -- 그 달 1일
  asset_class text not null,
  value_krw numeric not null,
  cost_krw numeric,
  primary key (month, asset_class)
);

-- 단순 설정값 (예: goal_total = 목표 총액)
create table if not exists app_settings (
  key text primary key,
  value jsonb not null
);

alter table cash_balances enable row level security;
alter table portfolio_snapshots enable row level security;
alter table app_settings enable row level security;
