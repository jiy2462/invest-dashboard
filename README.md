# 투자 대시보드

Streamlit + Supabase로 만든 개인 투자 대시보드. 설계 문서: https://claude.ai/artifact/VxNmfERcpHYfm18vgJuNun

## 화면
1. 오늘의 시장 — 커스텀 지표 카드
2. 관심종목·차트 — 종목 검색, 캔들차트, 관심종목
3. 포트폴리오 — 거래 입력, 보유 현황, 리밸런싱
4. 백테스트 — 자산군 조합, 최대 30년
5. 오늘의 인사이트 — 구루 명언 2개, 유튜브 추천
6. 지출관리 — 신한·현대카드 결제 알림(아이폰 단축어 → Edge Function `card-inbox`)을 모아 확인 체크

## 로컬 실행
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 값 채우기
streamlit run app.py
```

## 배포 (Streamlit Community Cloud)
- 저장소 연결 → Main file: `app.py`
- App settings → Secrets 에 `.streamlit/secrets.toml.example` 의 키를 실제 값으로 입력

## 로그인
`APP_PASSWORD` 하나로 로그인. 로그인하면 30일짜리 서명 토큰을 쿠키와 주소(`?t=...`)에 둬서 새로고침해도 유지된다.
`?t=`가 붙은 주소를 남에게 주면 그 사람도 들어올 수 있으니 공유하지 말 것. 비밀번호를 바꾸면 기존 로그인은 모두 풀린다.

## DB
Supabase 프로젝트 `invest-dashboard` (서울). 스키마는 `supabase/` 폴더의 SQL을 번호 순서대로 적용한다 (`001_init.sql` → `002_portfolio.sql` → `003_fx_exchanges.sql` → `004_backtest_runs.sql` → `005_insight.sql` → `006_trade_inbox.sql` → `007_card_expenses.sql`).

### 포트폴리오 계산 규칙
- 평단은 이동평균법: 매도해도 평단은 그대로, 차액은 실현손익.
- 외화 거래는 거래 당시 환율(`fx_rate`)로 원화 원가를 잡고, 현재 환율로 평가 → 원화 손익에 환차손익 포함.
- 배당은 `side='dividend'`, `quantity=0`, `price`=받은 총액.
- 티커 없는 자산(KRX 금현물 등)은 `MANUAL:이름` 티커로 저장하고 설정에서 현재가를 직접 입력.
- 현금성 자산(RP·CD·적금)은 `cash_balances`에 잔액만.
- 적립 계획(`lib/plan.py`, `app_settings` 키 `dca_plan`): 매주 합계 = (월 입금액 × 개월 + 목돈) ÷ 계획 기간의 매수 횟수. 지금 평가액에 남은 적립금을 더한 총액이 목표 비중이 되도록 부족한 자산군에 나눈다(매도 없음).
- 투자유보현금(계획의 `reserve` 자산군)은 월 입금액·목돈과 별개라 비중의 분모, 매주 적립액, 리밸런싱 계산에서 뺀다.
- 정기 리밸런싱은 매년 2월·8월 마지막 주 월요일. 투자 자산끼리 목표 비중으로 되돌리는 매수·매도 금액을 보여 준다.

### 백테스트 계산 규칙
- 시세는 yfinance(배당·분할 반영 종가)만 쓴다. 자산군마다 `lib/backtest.py`의 `CATALOG`에 '대표 ETF → 이전 기간 대체 펀드·지수' 순서를 두고, 앞 티커가 없는 기간은 뒤 티커의 일별 수익률로 이어 붙인다. 실제로 쓴 구간은 결과의 '데이터 출처' 탭에 나온다.
- 미국 초단기 국채는 BIL 이전을 3개월 국채 금리(^IRX) 이자로 채운다. 원/달러는 야후 KRW=X(2003년 말~) 이전을 FRED DEXKOUS로 채운다.
- 매달 첫 거래일에 월 적립금을 목표 비중대로 사고, 리밸런싱 주기(안 함·분기·6개월·1년)의 첫 거래일에 전체를 목표 비중으로 맞춘다.
- CAGR·변동성·MDD·샤프는 적립금 영향을 뺀 수익 지수로, IRR은 실제 현금흐름으로 계산한다.
- '원금 손실 → 원금 회복'은 평가금액이 누적 투자금보다 적어진 날부터 다시 넘어선 날까지의 가장 긴 기간(개월).
- 조합은 `backtest_presets` 표에 저장한다 (001_init.sql에 이미 있음).
- 실행 결과(고른 옵션 + 지표 + 주 단위 곡선)는 `backtest_runs` 표에 저장한다 (004_backtest_runs.sql).

### 오늘의 인사이트 규칙
- 명언: `quotes` 표(처음 열 때 `lib/quotes_data.py`의 초안을 넣음)에서 날짜를 씨앗으로 2개를 고른다. 최근 60일 안에 나온 명언, 같은 구루·같은 주제는 피한다. ‘명언 목록’ 탭에서 고치거나 끌 수 있다.
- 해설: Secrets에 `ANTHROPIC_API_KEY`가 있으면 Claude가 내 자산군 비중·목표 차이·수익률과 오늘 시장 지표를 보고 쓴다. 없거나 실패하면 주제별 규칙 기반 해설.
- 영상 모으기: Secrets에 `YOUTUBE_API_KEY`(YouTube Data API v3, 무료 하루 10,000단위)가 있으면 채널의 업로드 재생목록을 처음 한 번 끝까지(채널당 최대 2,000개) 읽어 `videos` 표에 넣고, 그 뒤로는 매일 최신 영상만 읽는다. 3분 이하(쇼츠)·라이브 예정·비공개 영상은 넣지 않는다. 채널별 진행 상황은 `app_settings`의 `youtube_catalog`. 키가 없거나 API가 실패하면 채널 RSS(최신 15개)를 읽어 매일 쌓는다.
- 추천은 두 묶음이다.
  - 채널 전체 영상에서 4개: 2주보다 오래된 영상 중 오늘 시장 상황(조정장, VIX, 금리·환율·금값 급변 등)·내 보유 자산 관련 단어가 제목·설명에 있는 상위 영상에서 2개, 나머지 2개는 무작위(날짜 씨앗이라 하루 동안 같음, ‘영상 다시 고르기’는 새로 뽑음). 이미 ‘봤어요’ 한 영상, 30일 안에 추천한 영상은 뺀다. Claude가 있으면 관련 상위 30개 + 무작위 15개 후보 중 4개를 고르고 이유를 쓴다.
  - 새 영상: 최근 14일 안에 올라온 영상(‘봤어요’ 한 것 제외). 관련도 높은 6개는 소개 문장(Claude 또는 단어 기반)과 함께 카드로, 나머지(최대 24개)는 목록으로 보여 준다. `daily_videos.made_by = 'new'`로 구분한다.
  - ‘좋아요’ 누른 채널은 가산점.
- 앱을 열었을 때 오늘 것이 없으면 그 자리에서 만들고, GitHub Actions(`.github/workflows/daily-insight.yml`)가 매일 06:30(한국 시간)에 미리 만든다. 저장소 Settings → Secrets and variables → Actions에 `SUPABASE_URL`, `SUPABASE_KEY`(선택 `ANTHROPIC_API_KEY`, `YOUTUBE_API_KEY`)를 넣어야 동작한다. 매일 DB를 쓰므로 Supabase 무료 프로젝트 일시정지도 막아 준다.

### 체결·배당 알림 대기함
- 아이폰 단축어(iOS 27 '알림' 개인 자동화)가 업비트 앱 알림, 카카오톡 알림(한국투자증권·메리츠증권·NH투자증권)을 받으면 알림 내용을 Edge Function `trade-inbox`(`supabase/functions/trade-inbox`)로 POST한다.
- 함수는 Edge Function 시크릿 `INBOX_TOKEN`(전송용 비밀번호)을 헤더 `x-inbox-token`으로 확인하고, `trade_inbox` 표에 원문을 쌓는다. 같은 회사·같은 내용이 10분 안에 또 오면 한 번만 저장. JWT 확인은 끔(`verify_jwt=false`).
- 본문은 JSON `{"title": "...", "subtitle": "...", "body": "..."}`(단축어 입력의 제목·부제·본문). 회사는 JSON `source` 또는 주소 끝 `&source=upbit|kis|meritz|nh`, 없으면 제목·내용으로 짐작한다. 단축어에서는 헤더 대신 주소 끝 `?token=`으로 비밀번호를 보내도 된다.
- 함수는 `filter.ts` 규칙으로 한 번 더 거른다: 카톡 자동화(kis·meritz·nh)는 알림 첫 줄(보낸 카톡 채널 이름)이 그 증권사여야 하고, 모든 알림은 체결·매수·매도·배당·분배금·주문 같은 말이 있어야 저장한다. 아이폰에서 카톡 알림 하나가 증권사 자동화 3개를 모두 실행해도 맞는 하나만 남는다.
- 포트폴리오 → ‘알림 대기함’ 탭에서 `lib/trade_inbox.py`의 읽기 규칙이 구분·종목·수량·단가·날짜·계좌를 미리 채우고, 사람이 확인해 [거래내역에 넣기]를 눌러야 `transactions`에 들어간다(메모 ‘알림 자동 입력’).

## 테스트 (네트워크 없이)
```bash
pip install pytest
python -m pytest tests
```
`tests/fakes.py`가 Supabase와 시세를 가짜로 바꿔서 화면을 Streamlit AppTest로 실행한다.
