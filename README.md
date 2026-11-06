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
Supabase 프로젝트 `invest-dashboard` (서울). 스키마는 `supabase/` 폴더의 SQL 순서대로 적용되어 있음.
