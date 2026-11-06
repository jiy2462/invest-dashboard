import streamlit as st
from supabase import Client, create_client


@st.cache_resource
def get_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def ping() -> bool:
    """DB 연결 확인용: 지표 설정 테이블을 한 줄 읽는다."""
    try:
        get_client().table("indicator_settings").select("id").limit(1).execute()
        return True
    except Exception:
        return False
