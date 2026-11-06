"""AppTest 진입점: 가짜를 설치하고 PAGE 환경변수로 지정한 화면을 실행한다."""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fakes  # noqa: E402

if not getattr(fakes, "_installed", False):
    fakes.install()
    fakes._installed = True
runpy.run_path(os.path.join(ROOT, "views", os.environ.get("PAGE", "market") + ".py"), run_name="__main__")
