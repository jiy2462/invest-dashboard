import pytest

from tests import fakes


@pytest.fixture(autouse=True)
def fresh():
    """매 테스트마다 가짜 DB를 비우고 네트워크 함수를 가짜로 바꾼다."""
    fakes.install()
    fakes._installed = True
