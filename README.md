# 투자 대시보드

Streamlit + Supabase로 만든 개인 투자 대시보드. 설계 문서: https://claude.ai/artifact/VxNmfERcpHYfm18vgJuNun

## 화면
1. 오늘의 시장 — 커스텀 지표 카드
2. 관심종목·차트 — 종목 검색, 캔들차트, 관심종목
3. 포트폴리오 — 거래 입력, 보유 현황, 리밸런싱
4. 백테스트 — 자산군 조합, 최대 30년
5. 오늘의 인사이트 — 구루 명언 2개, 유튜브 추천

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
Supabase 프로젝트 `invest-dashboard` (서울). 스키마는 `supabase/` 폴더의 SQL을 번호 순서대로 적용한다 (`001_init.sql` → `002_portfolio.sql` → `003_fx_exchanges.sql` → `004_backtest_runs.sql` → `005_insight.sql`).

### 포트폴리오 계산 규칙
- 평단은 이동평균법: 매도해도 평단은 그대로, 차액은 실현손익.
- 외화 거래는 거래 당시 환율(`fx_rate`)로 원화 원가를 잡고, 현재 환율로 평가 → 원화 손익에 환차손익 포함.
- 배당은 `side='dividend'`, `quantity=0`, `price`=받은 총액.
- 티커 없는 자산(KRX 금현물 등)은 `MANUAL:이름` 티커로 저장하고 설정에서 현재가를 직접 입력.
- 현금성 자산(RP·CD·적금)은 `cash_balances`에 잔액만.

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
- 영상: ‘채널 관리’에 넣은 채널의 RSS(최근 15개)에서 30일 안 영상만 본다. 오늘 시장 상황(조정장, VIX, 금리·환율·금값 급변 등)과 내 보유 자산 관련 단어가 제목에 있으면 점수를 더하고, 이미 ‘봤어요’ 한 영상과 14일 안에 추천한 영상은 뺀다. ‘좋아요’ 누른 채널은 가산점. Claude가 있으면 상위 20개 후보 중 3개를 고르고 이유를 쓴다.
- 앱을 열었을 때 오늘 것이 없으면 그 자리에서 만들고, GitHub Actions(`.github/workflows/daily-insight.yml`)가 매일 06:30(한국 시간)에 미리 만든다. 저장소 Settings → Secrets and variables → Actions에 `SUPABASE_URL`, `SUPABASE_KEY`(선택 `ANTHROPIC_API_KEY`)를 넣어야 동작한다. 매일 DB를 쓰므로 Supabase 무료 프로젝트 일시정지도 막아 준다.

## 테스트 (네트워크 없이)
```bash
pip install pytest
python -m pytest tests
```
`tests/fakes.py`가 Supabase와 시세를 가짜로 바꿔서 화면을 Streamlit AppTest로 실행한다.
