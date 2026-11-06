import streamlit as st

from lib.db import ping

st.title("오늘의 시장")
st.info("1단계에서 지표 카드와 지표 편집이 들어갑니다.")
st.caption("DB 연결: " + ("정상" if ping() else "실패 — Secrets의 SUPABASE_URL/SUPABASE_KEY를 확인하세요"))
