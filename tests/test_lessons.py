"""Tests hệ thống lesson: dataset 8 lesson, trạng thái, mở khóa, parser chọn
nhiều lesson, pool random/smart, SRS practice-only, subgroup Lesson 7/8."""

import datetime
import random

import pytest

from kana_rush.data import KanaDataset
from kana_rush.learn import LearnSession
from kana_rush.lessons import (
    LessonDataError,
    LessonDataset,
    LessonSelectionError,
    LessonStatus,
    RoundRobinDeck,
    check_lessons_reviewable,
    learned_pool,
    lesson_accuracy,
    lesson_due_count,
    lesson_mastered_count,
    lesson_pool,
    lesson_status,
    lesson_unlocked,
    multi_lesson_pool,
    next_lesson_to_learn,
    parse_lesson_selection,
    reviewable_lessons,
    smart_pick,
    smart_weight,
    srs_pool,
)
from kana_rush.models import (
    AnswerSource,
    KanaCard,
    KanaState,
    LessonProgress,
    SaveData,
)
from kana_rush.scheduler import Scheduler
from kana_rush.ui import Answer, UI, UIOptions

from conftest import NOW, push_result

UTC = datetime.timezone.utc
DAY = datetime.timedelta(days=1)


class CorrectUI(UI):
    """UI trả lời tự động đúng cho kana hiện trên màn hình."""

    def __init__(self, dataset: KanaDataset, options: UIOptions | None = None) -> None:
        super().__init__(options)
        self.dataset = dataset
        self.last_shown = ""
        self.comparison_romaji = None

    def show_kana(self, kana: str, sub: str = "") -> None:
        self.last_shown = kana

    def say(self, text: str = "", style: str | None = None) -> None:
        if isinstance(text, str) and "đọc là" in text:
            start = text.find("'", text.find("đọc là")) + 1
            end = text.find("'", start)
            if start > 0 and end > start:
                self.comparison_romaji = text[start:end]

    def press_enter(self, message: str = "Nhấn Enter để tiếp tục") -> None:
        return None

    def confirm(self, question: str, default_yes: bool = True) -> bool:
        return True

    def read_answer(self, prompt: str = "Romaji > ") -> Answer:
        if prompt.startswith("Chọn"):
            return Answer(kind="answer", text="1")
        if "Gõ lại romaji đúng" in prompt:
            return Answer(kind="answer", text=self.comparison_romaji or self._expected())
        if "Gõ lại" in prompt:
            return Answer(kind="answer", text=self._expected())
        return Answer(kind="answer", text=self._expected())

    def _expected(self) -> str:
        shown = self.last_shown.strip()
        kana_chars = [ch for ch in shown if ch in self.dataset.by_kana]
        if not kana_chars:
            return "a"
        return " ".join(self.dataset.by_kana[ch].romaji for ch in kana_chars)


class WrongUI(CorrectUI):
    """UI trả lời SAI mọi câu romaji nhưng vẫn hoàn thành corrective typing."""

    def read_answer(self, prompt: str = "Romaji > ") -> Answer:
        if prompt.startswith("Chọn"):
            return Answer(kind="answer", text="1")
        if "Gõ lại romaji đúng" in prompt:
            return Answer(kind="answer", text=self.comparison_romaji or self._expected())
        if "Gõ lại" in prompt:
            return Answer(kind="answer", text=self._expected())
        return Answer(kind="answer", text="zzz")


def unlock_lesson(
    save: SaveData,
    lesson,
    *,
    now=NOW,
    due: bool = False,
    mastered: bool = False,
) -> None:
    """Đưa toàn bộ kana của lesson lên REVIEW/MASTERED + đánh dấu học xong."""
    for kana_id in lesson.kana:
        card = save.card(kana_id)
        card.introduced_at = now - DAY
        card.state = KanaState.MASTERED if mastered else KanaState.REVIEW
        card.review_stage = 6 if mastered else 0
        card.next_review_at = (
            now if due else now + datetime.timedelta(days=10)
        )
    save.lesson_progress[lesson.id] = LessonProgress(
        lesson_id=lesson.id,
        introduced_kana=list(lesson.kana),
        completed_subgroups=list(range(lesson.group_count)),
        learn_completed=True,
        started_at=now - DAY,
        completed_at=now - DAY,
    )


@pytest.fixture(scope="module")
def lessons(dataset: KanaDataset) -> LessonDataset:
    return LessonDataset(dataset=dataset)


def review_card_for(kana: str, *, stage: int = 0, due: bool = True) -> KanaCard:
    card = KanaCard(state=KanaState.REVIEW, review_stage=stage)
    card.next_review_at = NOW - datetime.timedelta(hours=1) if due else NOW + datetime.timedelta(days=10)
    return card


# ------------------------------------------------------ dataset (tests 1-4)

def test_dataset_has_exactly_8_lessons(lessons: LessonDataset) -> None:
    assert len(lessons.lessons) == 8
    assert [l.id for l in lessons.lessons] == [1, 2, 3, 4, 5, 6, 7, 8]


def test_each_kana_in_exactly_one_lesson(lessons: LessonDataset, dataset: KanaDataset) -> None:
    from collections import Counter

    counts = Counter(k for lesson in lessons.lessons for k in lesson.kana)
    assert len(counts) == 46
    assert all(count == 1 for count in counts.values())


def test_total_kana_across_lessons_is_46(lessons: LessonDataset, dataset: KanaDataset) -> None:
    total = sum(len(l.kana) for l in lessons.lessons)
    assert total == 46
    all_kana = {k for l in lessons.lessons for k in l.kana}
    assert all_kana == set(dataset.by_kana)


def test_lesson7_8_have_two_subgroups(lessons: LessonDataset) -> None:
    lesson7 = lessons.by_id[7]
    lesson8 = lessons.by_id[8]
    assert len(lesson7.subgroups) == 2
    assert len(lesson8.subgroups) == 2
    assert tuple(k for g in lesson7.subgroups for k in g) == lesson7.kana
    assert tuple(k for g in lesson8.subgroups for k in g) == lesson8.kana
    assert len(lesson7.subgroups[0]) == 5 and len(lesson7.subgroups[1]) == 3
    assert len(lesson8.subgroups[0]) == 5 and len(lesson8.subgroups[1]) == 3


def test_lesson_data_error_on_bad_subgroups(tmp_path, dataset: KanaDataset) -> None:
    import json
    from kana_rush.data import DATA_DIR

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    raw = json.loads((DATA_DIR / "lessons.json").read_text(encoding="utf-8"))
    raw["lessons"][6]["subgroups"] = [["ま", "み", "む", "め", "も", "や"]]
    (data_dir / "lessons.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LessonDataError):
        LessonDataset(data_dir=data_dir, dataset=dataset)


# ------------------------------------------- lock/unlock/reviewable (tests 5-6)

def test_locked_lesson_cannot_be_reviewed(fresh_save, lessons: LessonDataset) -> None:
    with pytest.raises(LessonSelectionError):
        check_lessons_reviewable(fresh_save, lessons, [2])
    with pytest.raises(LessonSelectionError):
        check_lessons_reviewable(fresh_save, lessons, [7])
    # Lesson chưa bắt đầu (AVAILABLE) cũng không review được.
    with pytest.raises(LessonSelectionError):
        check_lessons_reviewable(fresh_save, lessons, [1])
    # Lesson đã mở khóa và đã bắt đầu thì review được.
    fresh_save.card("あ").state = KanaState.LEARNING
    check_lessons_reviewable(fresh_save, lessons, [1])


def test_started_lesson_can_be_reviewed(fresh_save, lessons: LessonDataset) -> None:
    fresh_save.card("あ").state = KanaState.LEARNING
    fresh_save.card("い").state = KanaState.REVIEW
    assert reviewable_lessons(fresh_save, lessons) == [lessons.by_id[1]]
    assert lesson_pool(fresh_save, lessons.by_id[1]) == ["あ", "い"]


# ------------------------------------------------------ parser (tests 7-11)

def test_parser_understands_comma_list() -> None:
    assert parse_lesson_selection("1,3,5") == [1, 3, 5]
    assert parse_lesson_selection(" 2 , 4 ") == [2, 4]


def test_parser_understands_range() -> None:
    assert parse_lesson_selection("1-4") == [1, 2, 3, 4]


def test_parser_understands_mixed_comma_and_range() -> None:
    assert parse_lesson_selection("1,3-5") == [1, 3, 4, 5]
    assert parse_lesson_selection("1,3-6") == [1, 3, 4, 5, 6]


def test_parser_deduplicates_and_sorts() -> None:
    assert parse_lesson_selection("5,1,5,3") == [1, 3, 5]
    assert parse_lesson_selection("2-4,3,1") == [1, 2, 3, 4]


def test_parser_rejects_invalid_ids() -> None:
    for bad in ("9", "0", "-1", "1-9", "abc", "", "1,", "2-1", "1--4"):
        with pytest.raises(LessonSelectionError):
            parse_lesson_selection(bad)


def test_parser_understands_all() -> None:
    assert parse_lesson_selection("all") == [1, 2, 3, 4, 5, 6, 7, 8]


def test_parser_normalizes_case_and_whitespace() -> None:
    assert parse_lesson_selection(" ALL ") == [1, 2, 3, 4, 5, 6, 7, 8]
    assert parse_lesson_selection(" 1 , 2 - 4 ") == [1, 2, 3, 4]


def test_multi_lesson_pool_order_follows_sorted_lesson_ids(fresh_save, lessons: LessonDataset) -> None:
    """Pool nhiều lesson sắp theo ID lesson (đã sort), kana giữ thứ tự trong lesson."""
    for kana in "あいうえお":
        fresh_save.cards[kana] = review_card_for(kana)
    for kana in "さし":
        fresh_save.cards[kana] = review_card_for(kana)
    unlock_lesson(fresh_save, lessons.by_id[1])
    unlock_lesson(fresh_save, lessons.by_id[2])
    pool = multi_lesson_pool(fresh_save, lessons, [3, 1])
    assert pool == ["あ", "い", "う", "え", "お", "さ", "し"]


# ------------------------------------------------------ pools (tests 12-13)

def test_multi_lesson_pool_only_selected_lessons(fresh_save, lessons: LessonDataset) -> None:
    for kana in "あいうえお":
        fresh_save.cards[kana] = review_card_for(kana)
    for kana in "かきく":
        fresh_save.cards[kana] = review_card_for(kana)
    unlock_lesson(fresh_save, lessons.by_id[1])
    # Lesson 3 chưa mở khóa nhưng kana vẫn đang REVIEW (ví dụ diagnostic):
    fresh_save.cards["さ"] = review_card_for("さ")
    pool = multi_lesson_pool(fresh_save, lessons, [1, 2])
    assert set(pool) == {"あ", "い", "う", "え", "お", "か", "き", "く"}
    assert "さ" not in pool
    assert "け" not in pool  # NEW
    assert len(pool) == 8


def test_random_learned_pool_excludes_unintroduced(fresh_save, lessons: LessonDataset) -> None:
    for kana in "あいうえお":
        fresh_save.cards[kana] = review_card_for(kana)
    fresh_save.cards["か"] = KanaCard(state=KanaState.LEARNING, introduced_at=NOW - DAY)
    fresh_save.cards["き"] = KanaCard(state=KanaState.NEW)
    unlock_lesson(fresh_save, lessons.by_id[1])
    # Kana của lesson LOCKED (dù ở REVIEW) không được lấy:
    fresh_save.cards["さ"] = review_card_for("さ")
    pool = learned_pool(fresh_save, lessons)
    assert set(pool) == {"あ", "い", "う", "え", "お", "か"}
    assert "き" not in pool
    assert "さ" not in pool


# ------------------------------------------------ deck random (tests 14-16)

def test_random_review_no_consecutive_repeat_when_options_remain() -> None:
    pool = ["あ", "い", "う", "え", "お"]
    deck = RoundRobinDeck(pool, random.Random(1))
    draws = [deck.draw() for _ in range(40)]
    assert all(draws[i] != draws[i + 1] for i in range(len(draws) - 1))


def test_random_review_exhausts_pool_before_new_round() -> None:
    pool = ["あ", "い", "う", "え", "お"]
    deck = RoundRobinDeck(pool, random.Random(3))
    round1 = [deck.draw() for _ in range(5)]
    assert set(round1) == set(pool)
    assert len(set(round1)) == 5
    round2 = [deck.draw() for _ in range(5)]
    assert set(round2) == set(pool)
    assert round2[0] != round1[-1]


def test_random_review_deterministic_with_fixed_seed() -> None:
    def run(seed: int) -> list[str]:
        deck = RoundRobinDeck(["あ", "い", "う", "え", "お"], random.Random(seed))
        return [deck.draw() for _ in range(30)]

    assert run(42) == run(42)
    assert run(42) != run(43)


# ------------------------------------------------------ smart random (test 17)

def test_smart_random_prefers_weak_kana_over_many_samples(fresh_save) -> None:
    weak = fresh_save.card("あ")
    weak.state = KanaState.RELEARNING
    weak.mastery_score = 0.1
    weak.recent_results = [
        {"ts": NOW.isoformat(), "session_id": "s", "correct": False, "hinted": False,
         "rt_ms": 5000, "confusion": None, "source": "review"}
    ]
    weak.response_times_ms = [5000] * 5
    weak.next_review_at = NOW - datetime.timedelta(hours=1)

    strong = fresh_save.card("い")
    strong.state = KanaState.REVIEW
    strong.mastery_score = 0.95
    strong.recent_results = [
        {"ts": NOW.isoformat(), "session_id": "s", "correct": True, "hinted": False,
         "rt_ms": 400, "confusion": None, "source": "review"}
    ]
    strong.response_times_ms = [400] * 5
    strong.next_review_at = NOW + datetime.timedelta(days=10)

    rng = random.Random(7)
    picks = [smart_pick(["あ", "い"], fresh_save, rng, NOW) for _ in range(300)]
    weak_picks = picks.count("あ")
    assert weak_picks > 200, f"kana yếu chỉ được chọn {weak_picks}/300"


def test_smart_weight_formula_ranks_weak_first(fresh_save) -> None:
    weak = fresh_save.card("あ")
    weak.state = KanaState.RELEARNING
    weak.mastery_score = 0.1
    weak.next_review_at = NOW - datetime.timedelta(hours=1)
    strong = fresh_save.card("い")
    strong.state = KanaState.REVIEW
    strong.mastery_score = 0.95
    strong.next_review_at = NOW + datetime.timedelta(days=10)
    assert smart_weight(weak, fresh_save, "あ", NOW) > smart_weight(strong, fresh_save, "い", NOW)


# --------------------------------------------- SRS practice-only (tests 18-19)

def test_random_practice_does_not_push_srs_for_not_due(dataset, fresh_save) -> None:
    scheduler = Scheduler(dataset)
    card = fresh_save.card("あ")
    card.state = KanaState.REVIEW
    card.review_stage = 2
    card.next_review_at = NOW + datetime.timedelta(days=10)
    outcome = scheduler.record_result(
        fresh_save, "あ", correct=True, hinted=False, rt_ms=500,
        session_id="s1", source=AnswerSource.RANDOM, now=NOW,
    )
    assert card.next_review_at == NOW + datetime.timedelta(days=10)
    assert card.review_stage == 2
    assert card.state is KanaState.REVIEW
    assert outcome.state_after is KanaState.REVIEW
    # Vẫn được ghi vào lịch sử/thống kê:
    assert card.recent_results[-1]["correct"] is True
    assert card.last_result()["source"] == "random"


def test_random_review_due_item_updates_srs_normally(dataset, fresh_save) -> None:
    scheduler = Scheduler(dataset)
    card = fresh_save.card("あ")
    card.state = KanaState.REVIEW
    card.review_stage = 0
    card.next_review_at = NOW - datetime.timedelta(hours=1)
    scheduler.record_result(
        fresh_save, "あ", correct=True, hinted=False, rt_ms=500,
        session_id="s1", source=AnswerSource.RANDOM, now=NOW,
    )
    assert card.state is KanaState.REVIEW
    assert card.review_stage > 0
    assert card.next_review_at > NOW


def test_random_wrong_due_item_goes_relearning(dataset, fresh_save) -> None:
    scheduler = Scheduler(dataset)
    card = fresh_save.card("あ")
    card.state = KanaState.REVIEW
    card.review_stage = 2
    card.next_review_at = NOW - datetime.timedelta(hours=1)
    scheduler.record_result(
        fresh_save, "あ", correct=False, hinted=False, rt_ms=3000,
        session_id="s1", source=AnswerSource.RANDOM, now=NOW,
    )
    assert card.state is KanaState.RELEARNING


# -------------------------------------------- unlock & status (tests 20-21)

def test_next_lesson_unlocks_without_mastery(fresh_save, lessons: LessonDataset) -> None:
    lesson1 = lessons.by_id[1]
    # Lesson 1 xong Learn (kana chỉ ở REVIEW, KHÔNG cần MASTERED):
    unlock_lesson(fresh_save, lesson1, due=True)
    assert all(
        fresh_save.card(k).state is KanaState.REVIEW for k in lesson1.kana
    )
    assert not any(
        fresh_save.card(k).state is KanaState.MASTERED for k in lesson1.kana
    )
    assert lesson_unlocked(fresh_save, lessons, 2)
    # Chưa xong lesson 1 thì lesson 2 khóa:
    assert not lesson_unlocked(SaveData(), lessons, 2)


def test_lesson_status_derived_from_kana(fresh_save, lessons: LessonDataset) -> None:
    lesson1 = lessons.by_id[1]
    lesson2 = lessons.by_id[2]
    # Mới bắt đầu: lesson 1 AVAILABLE, lesson 2 trở đi LOCKED.
    assert lesson_status(fresh_save, lessons, lesson1, NOW) is LessonStatus.AVAILABLE
    assert lesson_status(fresh_save, lessons, lesson2, NOW) is LessonStatus.LOCKED
    # Có kana đang LEARNING -> lesson LEARNING.
    fresh_save.card("あ").state = KanaState.LEARNING
    assert lesson_status(fresh_save, lessons, lesson1, NOW) is LessonStatus.LEARNING
    # Learn xong + có kana đến hạn -> REVIEW_DUE.
    unlock_lesson(fresh_save, lesson1, due=True)
    assert lesson_status(fresh_save, lessons, lesson1, NOW) is LessonStatus.REVIEW_DUE
    # Learn xong, chưa đến hạn -> COMPLETED.
    unlock_lesson(fresh_save, lesson1, due=False)
    assert lesson_status(fresh_save, lessons, lesson1, NOW) is LessonStatus.COMPLETED
    # Tất cả kana MASTERED -> MASTERED.
    unlock_lesson(fresh_save, lesson1, mastered=True)
    assert lesson_status(fresh_save, lessons, lesson1, NOW) is LessonStatus.MASTERED


def test_srs_pool_prioritizes_overdue_and_adds_recently_wrong(fresh_save) -> None:
    fresh_save.cards["あ"] = KanaCard(
        state=KanaState.REVIEW, review_stage=0, next_review_at=NOW - datetime.timedelta(days=3)
    )
    fresh_save.cards["い"] = KanaCard(
        state=KanaState.REVIEW, review_stage=0, next_review_at=NOW - datetime.timedelta(days=1)
    )
    fresh_save.cards["う"] = KanaCard(
        state=KanaState.REVIEW, review_stage=2, next_review_at=NOW + datetime.timedelta(days=10)
    )
    wrong = fresh_save.card("え")
    wrong.state = KanaState.REVIEW
    wrong.next_review_at = NOW + datetime.timedelta(days=5)
    wrong.recent_results = [
        {"ts": NOW.isoformat(), "session_id": "s", "correct": False, "hinted": False,
         "rt_ms": 900, "confusion": None, "source": "review"}
    ]
    pool = srs_pool(fresh_save, NOW, random.Random(1))
    assert pool[0] == "あ"  # quá hạn lâu nhất lên trước
    assert pool[1] == "い"
    assert set(pool) == {"あ", "い", "え"}  # え mới sai được thêm vào


# -------------------------------------------------- subgroup 7/8 (test 22)

def test_lesson7_subgroup_progress_saved_and_restored(
    dataset: KanaDataset, lessons: LessonDataset, scheduler: Scheduler, tmp_path
) -> None:
    from kana_rush.storage import Storage

    lesson7 = lessons.by_id[7]
    save = SaveData()
    # Subgroup 1 đã học xong ở phiên trước (5 kana vào REVIEW, tiến độ lưu):
    for kana_id in lesson7.subgroups[0]:
        card = save.card(kana_id)
        card.state = KanaState.REVIEW
        card.review_stage = 0
        card.next_review_at = NOW + datetime.timedelta(days=1)
        card.introduced_at = NOW - DAY
    save.lesson_progress[7] = LessonProgress(
        lesson_id=7,
        introduced_kana=list(lesson7.subgroups[0]),
        completed_subgroups=[0],
        learn_completed=False,
        started_at=NOW - DAY,
    )

    # Lưu và khôi phục (mô phỏng thoát game rồi mở lại):
    storage = Storage(tmp_path)
    storage.save(save)
    restored = storage.load()
    progress = restored.lesson_progress[7]
    assert progress.completed_subgroups == [0]
    assert not progress.learn_completed
    assert progress.introduced_kana == list(lesson7.subgroups[0])

    # Tiếp tục: chỉ học subgroup 2, sau đó Boss Round cả 8 kana.
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, restored, scheduler, random.Random(42), NOW, "s1", lesson7
    ).run()
    assert report.completed is True
    assert restored.lesson_progress[7].completed_subgroups == [0, 1]
    assert restored.lesson_progress[7].learn_completed is True
    assert all(
        restored.card(k).state is KanaState.REVIEW for k in lesson7.kana
    )
    assert all(
        restored.card(k).review_stage == 0 for k in lesson7.kana
    )


def test_lesson8_subgroups_full_learn(
    dataset: KanaDataset, lessons: LessonDataset, scheduler: Scheduler
) -> None:
    lesson8 = lessons.by_id[8]
    save = SaveData()
    for lesson in lessons.lessons:
        if lesson.id >= 8:
            continue
        unlock_lesson(save, lesson)
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, save, scheduler, random.Random(42), NOW, "s1", lesson8
    ).run()
    assert report.completed is True
    assert save.lesson_progress[8].completed_subgroups == [0, 1]
    assert all(save.card(k).state is KanaState.REVIEW for k in lesson8.kana)


# -------------------------------------------------- học dở / resume

def test_learn_resume_mixed_states(
    dataset: KanaDataset, fresh_save, scheduler: Scheduler, seeded_rng
) -> None:
    """Lesson mở lại với kana vừa NEW vừa LEARNING: học tiếp tới xong, không reset."""
    fresh_save.card("あ").state = KanaState.LEARNING
    fresh_save.card("あ").introduced_at = NOW - DAY
    fresh_save.card("あ").learning_step = 2
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1", ["あ", "い", "う"]
    ).run()
    assert report.completed is True
    assert all(fresh_save.card(k).state is KanaState.REVIEW for k in "あいう")
    assert fresh_save.card("あ").review_stage == 0


def test_learn_wrong_answers_never_complete(
    dataset: KanaDataset, fresh_save, scheduler: Scheduler, seeded_rng
) -> None:
    """Trả lời sai toàn bộ: lesson không hoàn thành, không kana nào vào REVIEW."""
    ui = WrongUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1", ["ぬ"]
    ).run()
    assert report.completed is False
    assert fresh_save.card("ぬ").state is KanaState.LEARNING
    assert not any(
        c.state is KanaState.REVIEW for c in fresh_save.cards.values()
    )


def test_learn_wrong_then_resume_completes(
    dataset: KanaDataset, fresh_save, scheduler: Scheduler, seeded_rng
) -> None:
    """Phiên trước trả lời sai, phiên sau học lại với câu trả lời đúng -> hoàn thành."""
    ui_wrong = WrongUI(dataset, UIOptions(delay_ms=0))
    LearnSession(
        ui_wrong, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1", ["ぬ"]
    ).run()
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1", ["ぬ"]
    ).run()
    assert report.completed is True
    assert fresh_save.card("ぬ").state is KanaState.REVIEW
    assert fresh_save.card("ぬ").review_stage == 0


def test_learn_report_lesson_fields(
    dataset: KanaDataset, lessons: LessonDataset, fresh_save, scheduler: Scheduler, seeded_rng
) -> None:
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1", lessons.by_id[1]
    ).run()
    assert report.lesson_id == 1
    assert report.completed_subgroups == [0]
    assert report.completed is True


# -------------------------------------------------- helpers & edge cases

def test_lesson_stats_helpers(fresh_save, lessons: LessonDataset) -> None:
    lesson1 = lessons.by_id[1]
    fresh_save.cards["あ"] = review_card_for("あ", due=True)
    fresh_save.cards["い"] = review_card_for("い", stage=2, due=False)
    fresh_save.cards["う"] = KanaCard(state=KanaState.MASTERED)
    push_result(fresh_save.card("あ"), correct=True, rt_ms=800)
    push_result(fresh_save.card("い"), correct=False, rt_ms=1200)
    assert lesson_due_count(fresh_save, lesson1, NOW) == 1
    assert lesson_mastered_count(fresh_save, lesson1) == 1
    assert lesson_accuracy(fresh_save, lesson1) == 0.5
    assert lesson_accuracy(fresh_save, lessons.by_id[2]) is None


def test_next_lesson_to_learn(fresh_save, lessons: LessonDataset) -> None:
    assert next_lesson_to_learn(fresh_save, lessons) is lessons.by_id[1]
    unlock_lesson(fresh_save, lessons.by_id[1])
    assert next_lesson_to_learn(fresh_save, lessons) is lessons.by_id[2]
    for lesson in lessons.lessons:
        unlock_lesson(fresh_save, lesson)
    assert next_lesson_to_learn(fresh_save, lessons) is None


def test_check_lessons_reviewable_mixed(fresh_save, lessons: LessonDataset) -> None:
    fresh_save.card("あ").state = KanaState.LEARNING
    check_lessons_reviewable(fresh_save, lessons, [1])
    with pytest.raises(LessonSelectionError, match="chưa mở khóa"):
        check_lessons_reviewable(fresh_save, lessons, [1, 3])
    unlock_lesson(fresh_save, lessons.by_id[1])
    # Lesson 2 đã mở khóa nhưng chưa bắt đầu -> từ chối.
    with pytest.raises(LessonSelectionError, match="chưa bắt đầu"):
        check_lessons_reviewable(fresh_save, lessons, [2])
    fresh_save.card("か").state = KanaState.REVIEW
    check_lessons_reviewable(fresh_save, lessons, [2])


def test_smart_pick_excludes_last_asked(fresh_save) -> None:
    for k in "あい":
        card = fresh_save.card(k)
        card.state = KanaState.REVIEW
        card.mastery_score = 0.9
        card.next_review_at = NOW + datetime.timedelta(days=10)
    rng = random.Random(5)
    picks = [
        smart_pick(["あ", "い"], fresh_save, rng, NOW, last_asked="あ")
        for _ in range(25)
    ]
    assert set(picks) == {"い"}


def test_deck_single_kana_pool_repeats() -> None:
    deck = RoundRobinDeck(["あ"], random.Random(1))
    draws = [deck.draw() for _ in range(5)]
    assert draws == ["あ"] * 5  # pool 1 kana: lặp liên tiếp là bắt buộc


def test_lesson_progress_storage_roundtrip(tmp_path) -> None:
    from kana_rush.storage import Storage

    progress = LessonProgress(
        lesson_id=7,
        introduced_kana=["ま", "み"],
        completed_subgroups=[0, 1],
        learn_completed=True,
        started_at=NOW,
        completed_at=NOW + DAY,
        last_practiced_at=NOW + DAY,
        total_attempts=40,
        accuracy=0.825,
    )
    save = SaveData()
    save.lesson_progress[7] = progress
    storage = Storage(tmp_path)
    storage.save(save)
    restored = storage.load().lesson_progress[7]
    assert restored.lesson_id == 7
    assert restored.introduced_kana == ["ま", "み"]
    assert restored.completed_subgroups == [0, 1]
    assert restored.learn_completed is True
    assert restored.started_at == NOW
    assert restored.completed_at == NOW + DAY
    assert restored.last_practiced_at == NOW + DAY
    assert restored.total_attempts == 40
    assert restored.accuracy == pytest.approx(0.825)


def test_srs_pool_excludes_new_kana(fresh_save) -> None:
    fresh_save.cards["あ"] = KanaCard(state=KanaState.NEW)
    fresh_save.cards["い"] = KanaCard(
        state=KanaState.LEARNING, next_review_at=NOW - datetime.timedelta(hours=1)
    )
    fresh_save.cards["う"] = KanaCard(
        state=KanaState.REVIEW, review_stage=0, next_review_at=NOW - datetime.timedelta(days=1)
    )
    pool = srs_pool(fresh_save, NOW, random.Random(1))
    assert "あ" not in pool
    assert set(pool) == {"い", "う"}


def test_legacy_save_reopen_all_promoted_lesson_marks_complete(
    dataset: KanaDataset, lessons: LessonDataset, scheduler: Scheduler
) -> None:
    """Save cũ: kana đã vào REVIEW nhưng chưa có lesson_progress. Mở lại lesson
    và học (boss replay) phải ghi nhận learn_completed -> mở khóa lesson sau."""
    save = SaveData()
    lesson1 = lessons.by_id[1]
    lesson2 = lessons.by_id[2]
    for kana_id in lesson1.kana:
        card = save.card(kana_id)
        card.state = KanaState.REVIEW
        card.review_stage = 1
        card.next_review_at = NOW + datetime.timedelta(days=1)
        card.introduced_at = NOW - DAY
    assert save.lesson_progress == {}  # save cũ không có lesson_progress
    assert lesson_unlocked(save, lessons, 2)  # fallback theo trạng thái kana
    assert not lesson_unlocked(save, lessons, 3)

    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    report = LearnSession(
        ui, dataset, save, scheduler, random.Random(42), NOW, "s1", lesson2
    ).run()
    assert report.completed is True
    assert save.lesson_progress[2].learn_completed is True
    assert save.lesson_progress[2].completed_subgroups == [0]
    assert lesson_unlocked(save, lessons, 3)
