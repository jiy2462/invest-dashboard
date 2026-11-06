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

## DB
Supabase 프로젝트 `invest-dashboard` (서울). 스키마는 `supabase/` 폴더의 SQL을 번호 순서대로 적용한다 (`001_init.sql` → `002_portfolio.sql` → `003_fx_exchanges.sql`).

### 포트폴리오 계산 규칙
- 평단은 이동평균법: 매도해도 평단은 그대로, 차액은 실현손익.
- 외화 거래는 거래 당시 환율(`fx_rate`)로 원화 원가를 잡고, 현재 환율로 평가 → 원화 손익에 환차손익 포함.
- 배당은 `side='dividend'`, `quantity=0`, `price`=받은 총액.
- 티커 없는 자산(KRX 금현물 등)은 `MANUAL:이름` 티커로 저장하고 설정에서 현재가를 직접 입력.
- 현금성 자산(RP·CD·적금)은 `cash_balances`에 잔액만.

## 테스트 (네트워크 없이)
```bash
pip install pytest
python -m pytest tests
```
`tests/fakes.py`가 Supabase와 시세를 가짜로 바꿔서 화면을 Streamlit AppTest로 실행한다.
