import os

import streamlit as st
from supabase import Client, create_client


def secret(name: str, default: str = "") -> str:
    """Streamlit secrets → 환경변수 순서로 찾는다 (GitHub Actions 예약 작업은 환경변수를 쓴다)."""
    try:
        v = st.secrets.get(name)
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get(name, default)


@st.cache_resource
def get_client() -> Client:
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY"))


def ping() -> bool:
    """DB 연결 확인용: 지표 설정 테이블을 한 줄 읽는다."""
    try:
        get_client().table("indicator_settings").select("id").limit(1).execute()
        return True
    except Exception:
        return False
