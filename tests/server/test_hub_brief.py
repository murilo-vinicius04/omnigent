from __future__ import annotations

from omnigent.server import hub_brief
from omnigent.server.hub_brief import (
    AUTO_DONE_KEPT,
    AUTO_OPEN_MAX,
    PROGRESS_KEPT,
    Living,
    Report,
    Update,
    apply_update,
    build_task,
    living_from,
    parse_update,
    with_ids,
)


def _item(content: str, *, status: str = "open", by: str = "you", id: str = "") -> dict:
    item = {"content": content, "status": status, "by": by}
    if id:
        item["id"] = id
    return item


# -- parse_update ----------------------------------------------------------------


def test_parse_update_reads_a_plain_object() -> None:
    update = parse_update(
        '{"overview": "A scanner.", "status": "Tests pass.", "progress": "Fixed it.",'
        ' "todo_add": ["Calibrate"], "todo_done": ["ab12"], "todo_drop": []}'
    )
    assert update == Update(
        overview="A scanner.",
        status="Tests pass.",
        progress="Fixed it.",
        add=("Calibrate",),
        done=("ab12",),
    )


def test_parse_update_finds_the_object_inside_fences_and_chatter() -> None:
    reply = 'Sure!\n```json\n{"status": "Waiting for you."}\n```\nAnything else?'
    assert parse_update(reply) == Update(status="Waiting for you.")


def test_parse_update_gives_up_on_garbage_and_empty_objects() -> None:
    assert parse_update("no json here") is None
    assert parse_update("{not json}") is None
    assert parse_update("[1, 2]") is None
    assert parse_update('{"overview": "", "todo_add": []}') is None


def test_parse_update_ignores_wrong_types_and_collapses_whitespace() -> None:
    update = parse_update(
        '{"status": "Line one\\n  line two", "progress": 7,'
        ' "todo_add": ["ok", 3, "", "  "], "todo_done": "ab12"}'
    )
    assert update == Update(status="Line one line two", add=("ok",))


# -- living_from / with_ids -------------------------------------------------------


def test_living_from_forgives_missing_and_wrong_shapes() -> None:
    assert living_from(None) == Living()
    assert living_from("x") == Living()
    stored = living_from(
        {
            "overview": " What it is ",
            "status": 5,
            "progress": [{"at": 3, "text": "Did a thing", "lane": "sim"}, {"text": ""}, "x"],
            "updated_at": "9",
        }
    )
    assert stored.overview == "What it is"
    assert stored.status == "5"
    assert [p["text"] for p in stored.progress] == ["Did a thing"]
    assert stored.updated_at == 9.0


def test_with_ids_keeps_existing_ids_and_never_repeats() -> None:
    items = with_ids([_item("a", id="keep"), _item("b"), _item("c")])
    ids = [i["id"] for i in items]
    assert ids[0] == "keep" and len(set(ids)) == 3 and all(ids)


# -- apply_update ------------------------------------------------------------------


def test_apply_update_writes_overview_status_and_progress() -> None:
    living, todos = apply_update(
        Living(),
        [],
        Update(overview="A scanner.", status="Tests pass.", progress="Fixed it."),
        lane="sim",
        pinned=False,
        now=100.0,
    )
    assert (living.overview, living.status) == ("A scanner.", "Tests pass.")
    assert living.progress == ({"at": 100.0, "text": "Fixed it.", "lane": "sim"},)
    assert living.updated_at == 100.0 and todos == []


def test_apply_update_leaves_a_pinned_overview_alone() -> None:
    living, _ = apply_update(
        Living(overview="Mine"), [], Update(overview="Theirs", status="s"), lane="", pinned=True
    )
    assert living.overview == "Mine" and living.status == "s"


def test_an_answer_with_no_status_keeps_the_old_one() -> None:
    living, _ = apply_update(
        Living(status="Old"), [], Update(progress="Did x"), lane="", pinned=False
    )
    assert living.status == "Old"


def test_progress_is_newest_first_deduplicated_and_capped() -> None:
    living = Living()
    for n in range(PROGRESS_KEPT + 3):
        living, _ = apply_update(living, [], Update(progress=f"step {n}"), lane="", pinned=False)
    assert len(living.progress) == PROGRESS_KEPT
    assert living.progress[0]["text"] == f"step {PROGRESS_KEPT + 2}"
    again, _ = apply_update(
        living, [], Update(progress=f"STEP {PROGRESS_KEPT + 2}"), lane="", pinned=False
    )
    assert len(again.progress) == PROGRESS_KEPT


def test_new_items_get_ids_and_are_the_companions() -> None:
    _, todos = apply_update(
        Living(), [], Update(add=("Calibrate", "Film it")), lane="", pinned=False
    )
    assert [(t["content"], t["status"], t["by"]) for t in todos] == [
        ("Calibrate", "open", "companion"),
        ("Film it", "open", "companion"),
    ]
    assert all(t["id"] for t in todos)


def test_an_item_already_listed_is_not_added_again_even_when_done() -> None:
    mine = [_item("Calibrate the arm", id="a"), _item("Film it", status="done", id="b")]
    _, todos = apply_update(
        Living(), mine, Update(add=("calibrate  THE arm", "film it", "New")), lane="", pinned=False
    )
    assert [t["content"] for t in todos] == ["Calibrate the arm", "Film it", "New"]


def test_the_companion_can_tick_any_item_but_drop_only_its_own() -> None:
    todos = [
        _item("Mine", id="m1"),
        _item("Mine too", id="m2"),
        _item("Its", by="companion", id="c1"),
    ]
    _, out = apply_update(
        Living(), todos, Update(done=("m1", "nope"), drop=("m2", "c1")), lane="", pinned=False
    )
    assert [(t["id"], t["status"]) for t in out] == [("m1", "done"), ("m2", "open")]


def test_the_companion_stops_adding_at_its_open_limit() -> None:
    full = [_item(f"item {n}", by="companion") for n in range(AUTO_OPEN_MAX)]
    _, out = apply_update(Living(), full, Update(add=("one more",)), lane="", pinned=False)
    assert len(out) == AUTO_OPEN_MAX
    mine = [_item("Mine", id="m")]
    _, with_mine = apply_update(Living(), full + mine, Update(add=("x",)), lane="", pinned=False)
    assert len(with_mine) == AUTO_OPEN_MAX + 1


def test_only_its_oldest_finished_items_are_forgotten() -> None:
    old = [
        _item(f"done {n}", status="done", by="companion", id=f"d{n}")
        for n in range(AUTO_DONE_KEPT + 2)
    ]
    mine = [_item("Mine done", status="done", id="mine")]
    _, out = apply_update(Living(), mine + old, Update(status="s"), lane="", pinned=False)
    ids = [t["id"] for t in out]
    assert "mine" in ids
    assert ids.count("d0") == 0 and ids.count("d1") == 0
    assert sum(1 for i in ids if i.startswith("d")) == AUTO_DONE_KEPT


# -- build_task ---------------------------------------------------------------------


def test_build_task_shows_the_brief_the_list_and_what_was_reported() -> None:
    task = build_task(
        project="amarras",
        language="Brazilian Portuguese (pt-BR)",
        living=Living(
            overview="Chain docs.", status="Half done.", progress=({"text": "Wrote A"},)
        ),
        todos=[
            _item("Review", id="r1"),
            _item("Send", by="companion", id="s1", status="done"),
        ],
        report=Report(
            ask="finish the docs",
            told="It finished chapter two.",
            reply="Chapter two is in.",
            lane="docs",
            others=("sim: working",),
        ),
        pinned=False,
    )
    for needle in (
        "amarras",
        "Brazilian Portuguese",
        "Chain docs.",
        "Half done.",
        "- Wrote A",
        "[r1] open (you) Review",
        "[s1] done (companion) Send",
        "- sim: working",
        "finish the docs",
        "It finished chapter two.",
        "Chapter two is in.",
        hub_brief._OVERVIEW_OPEN,
    ):
        assert needle in task
    assert hub_brief._OVERVIEW_PINNED not in task


def test_build_task_tells_the_companion_to_leave_a_pinned_overview() -> None:
    task = build_task(
        project="p",
        language="English",
        living=Living(),
        todos=[],
        report=Report(),
        pinned=True,
    )
    assert hub_brief._OVERVIEW_PINNED in task
    assert "(not written yet)" in task and "(none yet)" in task
