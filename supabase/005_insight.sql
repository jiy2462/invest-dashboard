-- 5단계: 오늘의 인사이트 (명언 + 유튜브 추천). 표는 001_init.sql에 이미 있고, 열만 더한다.

-- 명언: 투자 철학 풀이(AI 없이도 보여 줄 기본 해설), 켜고 끄기(검토용)
alter table quotes add column if not exists philosophy text;
alter table quotes add column if not exists is_active boolean not null default true;
create unique index if not exists quotes_guru_quote_ko on quotes (guru, quote_ko);

-- 날짜별 명언: 보여 줄 순서, 해설을 누가 만들었는지(claude | basic)
alter table daily_quotes add column if not exists rank int not null default 1;
alter table daily_quotes add column if not exists made_by text;
alter table daily_quotes add column if not exists created_at timestamptz not null default now();

-- 채널: 추가한 때
alter table youtube_channels add column if not exists created_at timestamptz not null default now();

-- 영상: 채널을 지우면 영상·추천도 같이 지운다
alter table videos drop constraint if exists videos_channel_id_fkey;
alter table videos add constraint videos_channel_id_fkey
  foreign key (channel_id) references youtube_channels(channel_id) on delete cascade;
alter table daily_videos drop constraint if exists daily_videos_video_id_fkey;
alter table daily_videos add constraint daily_videos_video_id_fkey
  foreign key (video_id) references videos(video_id) on delete cascade;
alter table daily_videos add column if not exists made_by text;
