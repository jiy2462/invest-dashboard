-- 4단계: 백테스트 실행 결과 저장 (고른 옵션 + 결과 지표·곡선)
create table if not exists backtest_runs (
  id bigint generated always as identity primary key,
  name text not null,
  settings jsonb not null,      -- 기간, 통화, 초기 금액, 월 적립, 리밸런싱, 비교 기준, 조합별 비중
  results jsonb not null,       -- 조합별 지표와 주 단위 평가금액 곡선, 데이터 출처
  created_at timestamptz not null default now()
);

alter table backtest_runs enable row level security;
