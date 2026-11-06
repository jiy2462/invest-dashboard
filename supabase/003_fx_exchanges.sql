-- 달러 환전 기록 (시트 '메리츠증권 외화환전' 탭): 평균 환전 환율을 보기 위한 기록. 보유 자산 합계에는 넣지 않는다.
create table if not exists fx_exchanges (
  id bigint generated always as identity primary key,
  trade_date date not null,
  side text not null check (side in ('buy', 'sell')),   -- buy = 원화→외화 환전, sell = 외화→원화 재환전
  currency text not null default 'USD',
  amount numeric not null,                               -- 외화 금액
  rate numeric not null,                                 -- 원/외화
  fee numeric not null default 0,                        -- 원화 수수료
  account_id bigint references accounts(id) on delete set null,
  memo text,
  created_at timestamptz not null default now()
);

alter table fx_exchanges enable row level security;
