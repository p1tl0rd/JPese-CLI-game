"""Fixtures dùng chung cho toàn bộ test suite."""

import datetime
import random

import pytest

from kana_rush.data import KanaDataset
from kana_rush.models import AnswerSource, KanaCard, KanaState, SaveData
from kana_rush.scheduler import Scheduler

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def dataset() -> KanaDataset:
    return KanaDataset()


@pytest.fixture
def fresh_save() -> SaveData:
    return SaveData()


@pytest.fixture
def scheduler(dataset: KanaDataset) -> Scheduler:
    return Scheduler(dataset)


@pytest.fixture
def seeded_rng() -> random.Random:
    return random.Random(42)


def push_result(
    card: KanaCard,
    *,
    correct: bool,
    hinted: bool = False,
    rt_ms: int = 1000,
    session_id: str = "s1",
    source: str = "review",
    confusion: str | None = None,
    now: datetime.datetime = NOW,
) -> None:
    card.append_result(
        correct=correct,
        hinted=hinted,
        rt_ms=rt_ms,
        session_id=session_id,
        source=AnswerSource(source),
        confusion=confusion,
        now=now,
    )


def review_card(*, stage: int = 0, due: bool = True, state: str = "review") -> KanaCard:
    card = KanaCard(state=KanaState(state))
    card.review_stage = stage
    card.next_review_at = NOW - datetime.timedelta(hours=1) if due else NOW + datetime.timedelta(days=10)
    return card
