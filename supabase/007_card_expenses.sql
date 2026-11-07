-- 지출관리: 신한카드·현대카드 앱 푸시 알림을 아이폰 단축어(iOS 27 알림 자동화)가 보내면
-- Edge Function `card-inbox`가 원문을 여기에 쌓고, 앱(지출관리 탭)이 일시·거래처·금액을 읽어 채운다.
-- 전송용 비밀번호는 체결 알림과 같은 INBOX_TOKEN을 쓴다.

create table if not exists card_expenses (
  id bigint generated always as identity primary key,
  received_at timestamptz not null default now(),
  card text not null default 'other',          -- shinhan | hyundai | other
  title text,
  body text not null,
  raw jsonb,                                   -- 단축어가 보낸 그대로(읽기 규칙을 다듬을 때 참고)
  -- 앱이 알림 글에서 읽어 채우는 값 (못 읽으면 비어 있음)
  kind text check (kind in ('pay', 'cancel')), -- 결제 | 취소 (비어 있으면 결제 알림이 아님)
  spent_at timestamptz,                        -- 결제(취소) 일시
  merchant text,                               -- 거래처
  amount numeric,                              -- 금액(항상 양수, 취소는 kind로 구분)
  currency text not null default 'KRW',
  installment text,                            -- 일시불 / 3개월 …
  parse_version int not null default 0,        -- 읽기 규칙 버전(규칙이 바뀌면 안 고친 줄은 다시 읽음)
  edited boolean not null default false,       -- 사람이 값을 고쳤으면 다시 읽지 않음
  memo text,
  checked boolean not null default false,      -- 확인 완료
  checked_at timestamptz
);
create index if not exists card_expenses_spent on card_expenses (spent_at desc nulls first, received_at desc);

-- 앱은 서버 쪽 비밀 키로만 접근한다(다른 표와 같이 RLS만 켜고 정책은 두지 않음).
alter table card_expenses enable row level security;
