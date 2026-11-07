-- 체결·배당 알림 대기함: 아이폰 단축어(iOS 27 알림 자동화)가 보낸 알림 원문을 쌓아 두고,
-- 포트폴리오 탭에서 확인해 [거래내역에 넣기]를 누르면 transactions에 들어간다.
-- 알림은 Edge Function `trade-inbox`가 받는다(전송용 비밀번호 INBOX_TOKEN으로 확인).

create table if not exists trade_inbox (
  id bigint generated always as identity primary key,
  received_at timestamptz not null default now(),
  source text not null default 'other',      -- upbit | kis | meritz | nh | other
  title text,
  body text not null,
  raw jsonb,                                 -- 단축어가 보낸 그대로(규칙을 다듬을 때 참고)
  status text not null default 'pending' check (status in ('pending', 'done', 'ignored')),
  transaction_id bigint references transactions(id) on delete set null,
  handled_at timestamptz
);
create index if not exists trade_inbox_status_received on trade_inbox (status, received_at desc);

-- 앱은 서버 쪽 비밀 키로만 접근한다(다른 표와 같이 RLS만 켜고 정책은 두지 않음).
alter table trade_inbox enable row level security;
