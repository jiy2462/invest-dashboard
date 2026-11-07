"""매일 아침 예약 작업: 오늘의 명언 2개와 추천 영상(전체 영상 중 4개 + 최근 2주 새 영상)을 미리 만들어 Supabase에 저장한다.

GitHub Actions(.github/workflows/daily-insight.yml)가 한국 시간 아침 6시 30분에 실행한다.
필요한 환경변수: SUPABASE_URL, SUPABASE_KEY (선택: ANTHROPIC_API_KEY, ANTHROPIC_MODEL, YOUTUBE_API_KEY)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import insight  # noqa: E402


def main() -> int:
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY")):
        print("SUPABASE_URL / SUPABASE_KEY가 없어 건너뜁니다. GitHub 저장소 Settings → Secrets에 넣어 주세요.")
        return 0
    res = insight.ensure_today()
    print(f"{res['date']}: 새로 만든 것 = {res['made'] or '없음(이미 있음)'}")
    for q in res["quotes"]:
        print(f"  명언: {q['guru']} — {q['quote_ko']} ({q.get('made_by')})")
    for v in res["videos"]:
        kind = "새 영상" if v.get("kind") == "new" else "추천"
        print(f"  {kind}: {v['title']} ({v.get('made_by')})")
    for e in res["errors"]:
        print(f"  알림: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
