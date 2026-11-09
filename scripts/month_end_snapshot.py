"""월말 예약 작업: 매달 말일 밤 11시 59분(한국 시간)에 전체 계좌 기준 자산군별 평가금액·원금을 저장한다.

GitHub Actions(.github/workflows/month-end-snapshot.yml)가 28~31일 밤에 실행하고, 오늘이 그 달 말일일 때만 저장한다.
예약 실행이 늦어져 자정을 넘기면(다음 달 1일 새벽 6시 전) 지난달 기록이 없을 때만 지난달로 저장한다.
저장 전에 현금성 자산 자동 적립(적금 자동이체 등)이 밀려 있으면 먼저 반영한다.

필요한 환경변수: SUPABASE_URL, SUPABASE_KEY
수동 실행: python scripts/month_end_snapshot.py --force  (오늘 날짜의 달로 바로 저장)
"""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import portfolio as pf  # noqa: E402
from lib import valuation  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def target_month(now: datetime, has_snapshot, force: bool = False) -> date | None:
    """저장할 달(그 달 1일). 저장할 때가 아니면 None."""
    today = now.date()
    if force or (today + timedelta(days=1)).day == 1:
        return today.replace(day=1)
    if today.day == 1 and now.hour < 6:
        prev = (today - timedelta(days=1)).replace(day=1)
        return None if has_snapshot(prev) else prev
    return None


def main(argv: list[str]) -> int:
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY")):
        print("SUPABASE_URL / SUPABASE_KEY가 없어 건너뜁니다. GitHub 저장소 Settings → Secrets에 넣어 주세요.")
        return 0
    now = datetime.now(KST)
    snaps = pf.list_snapshots()
    done = set(str(m)[:7] for m in snaps["month"])
    month = target_month(now, lambda m: m.isoformat()[:7] in done, force="--force" in argv)
    if month is None:
        print(f"{now:%Y-%m-%d %H:%M} KST: 말일이 아니라 건너뜁니다.")
        return 0
    month_end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    for d in pf.apply_auto_deposits(min(now.date(), month_end)):   # 다음 달 적립분은 넣지 않는다
        print(f"  자동 적립: {d['name']} +₩{d['added']:,.0f} ({', '.join(d['months'])})")
    summary = valuation.current_summary(now.date())
    pf.save_snapshot(month, summary)
    print(f"{month:%Y-%m} 기록 저장: 평가금액 ₩{summary['value_krw'].sum():,.0f} · 원금 ₩{summary['cost_krw'].sum():,.0f}")
    for _, r in summary.iterrows():
        print(f"  {r['asset_class']}: ₩{r['value_krw']:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
