"""Messages for a busy lane: placed by the router, or asked, and every answer kept."""

from __future__ import annotations

import json
import time

import pytest

from omnigent.server import hub as hub_module
from omnigent.server import hub_chat, hub_place, model_router
from omnigent.server.hub import HubEntry, TurnState, parse_decision
from tests.server.test_hub import _hub, _Router, _settle, _their_words
from tests.server.test_hub_lanes import NEW, SIM, _LaneServer, _two_lanes


def _placements(tmp_path) -> list[dict]:
    path = tmp_path / "u1" / hub_place.LOG_NAME
    lines = path.read_text().splitlines() if path.exists() else []
    return [json.loads(line) for line in lines]


@pytest.fixture
def trusted(monkeypatch):
    """The router has earned deciding alone: its places matched their answers."""
    monkeypatch.setattr(hub_place, "trusted", lambda path, place="": True)


def _busy(tmp_path, *replies: str):
    router = _Router(*replies)
    server = _LaneServer()
    _two_lanes(server)
    server.turns = [TurnState(status="running")]
    return router, server, _hub(tmp_path, router, server)


def test_a_send_carries_its_place_and_a_correction_is_read() -> None:
    sent = parse_decision(
        '{"send": "5151", "message": "Plan it.", "place": "new", "topic": "joystick plan",'
        ' "use": "Sonnet", "say": "Abri."}'
    )
    assert (sent.place, sent.topic, sent.use) == ("new", "joystick plan", "Sonnet")
    assert parse_decision('{"send": "5151", "message": "x", "place": "later"}').place == ""
    fixed = parse_decision('{"correct_place": "steer", "say": ""}')
    assert (fixed.action, fixed.place) == ("correct_place", "steer")


async def test_a_direction_for_the_busy_lane_asks_first(tmp_path, trusted) -> None:
    # Confidence mode is off: even a steer guess the router earned trust for
    # waits for their choice instead of going into the lane's work at once.
    router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Pause the tuning; the lanes come first.", "place": "steer",'
        ' "say": "Entrou agora no que ele está fazendo."}',
    )
    hub.submit("fala pra ele parar o tuning e focar nas lanes")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))

    assert server.sent == []
    asked = hub.state.entries[-1]
    assert (asked.place["picked"], asked.place["guess"]) == ("ask", "steer")
    assert asked.text.startswith("Where should this go")
    (row,) = _placements(tmp_path)
    assert row["placement"]["id"] == asked.place["id"]
    assert row["placement"]["task"] == "tune the MPC gains"
    assert row["placement"]["said"] == "fala pra ele parar o tuning e focar nas lanes"

    entry = await hub.correct_place(asked.place["id"], "steer", via="button")
    await hub.close()

    assert entry is not None and entry.kind == "sent"
    assert server.sent == [
        (
            SIM,
            _their_words(
                "fala pra ele parar o tuning e focar nas lanes",
                "Pause the tuning; the lanes come first.",
            ),
        )
    ]
    assert hub.state.held == {}
    # The router reads where steering, waiting and new lanes apply.
    assert '"steer" -- it is about the task the lane is doing now' in router.prompts[0]


async def test_a_next_step_asks_first_and_waits_on_choice(tmp_path, trusted) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Then plot the gains.", "place": "after",'
        ' "say": "Vai quando terminar."}',
    )
    hub.submit("quando terminar plota os ganhos")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))

    assert server.sent == []
    held = hub.state.entries[-1]
    assert (held.place["picked"], held.place["guess"]) == ("ask", "after")
    assert hub.state.held[SIM]["items"][0]["place"] == held.place["id"]

    entry = await hub.correct_place(held.place["id"], "after", via="button")
    await hub.close()

    assert entry is not None and entry.text.startswith("Ok: it goes to")
    assert server.sent == [] and hub.state.held[SIM]["items"]


async def test_a_different_task_asks_first_and_opens_its_lane_on_choice(tmp_path, trusted) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Plan the joystick experiment.", "place": "new",'
        ' "topic": "joystick plan", "say": "Abri uma lane nova."}',
    )
    hub.submit("monta um plano de experimento pro joystick")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))

    assert server.sent == [] and server.opened == []
    asked = hub.state.entries[-1]
    assert (asked.place["picked"], asked.place["guess"]) == ("ask", "new")

    entry = await hub.correct_place(asked.place["id"], "new", via="button")
    await hub.close()

    assert entry is not None and entry.session_id == NEW
    ((_, spec),) = server.opened
    assert spec.topic == "joystick plan"
    ((to, text),) = server.sent
    assert to == NEW and text.startswith("monta um plano de experimento pro joystick")
    assert "Plan the joystick experiment." in text


async def test_a_now_they_did_not_ask_for_is_held_and_asked(tmp_path, trusted) -> None:
    # A hallucinated "now" flag must not steer a busy lane: without their
    # tell-it-now words it goes through placing, which asks.
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Stop the tuning.", "place": "steer",'
        ' "say": "Sent.", "now": true}',
    )
    hub.submit("para o tuning")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    assert server.sent == []
    asked = hub.state.entries[-1]
    assert (asked.place["picked"], asked.place["guess"]) == ("ask", "steer")


async def test_without_a_place_a_message_for_a_busy_lane_waits(tmp_path, trusted) -> None:
    _router, server, hub = _busy(tmp_path, '{"send": "5151", "message": "Log them."}')
    hub.submit("loga os ganhos")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    assert server.sent == []
    assert _placements(tmp_path)[0]["placement"]["by"] == "default"


async def test_a_held_message_they_correct_goes_in_now_and_becomes_a_lesson(
    tmp_path, trusted
) -> None:
    router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Use the right bench only.", "place": "after"}',
        '{"answer": "ok"}',
    )
    hub.submit("usa só a bancada da direita")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    place_id = hub.state.entries[-1].place["id"]

    entry = await hub.correct_place(place_id, "steer", via="button")
    assert entry is not None and entry.kind == "sent" and entry.place["picked"] == "steer"
    assert server.sent == [
        (SIM, _their_words("usa só a bancada da direita", "Use the right bench only."))
    ]
    assert hub.state.held == {}

    # From then on the router is shown the correction.
    hub.submit("oi")
    await _settle(hub, until=lambda: len(router.prompts) == 2)
    await hub.close()
    assert (
        '"usa só a bancada da direita": it belonged "steer" '
        '(the hub asked; it had guessed "after").'
    ) in router.prompts[1]
    assert _placements(tmp_path)[-1] == {
        "feedback": place_id,
        "right": "steer",
        "via": "button",
        "at": _placements(tmp_path)[-1]["at"],
    }


async def test_a_held_message_that_was_a_new_task_opens_its_lane(tmp_path, trusted) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Plan the joystick.", "place": "after",'
        ' "topic": "joystick plan"}',
    )
    hub.submit("planeja o joystick")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))

    entry = await hub.correct_place(hub.state.entries[-1].place["id"], "new", via="button")
    await hub.close()

    assert entry is not None and entry.session_id == NEW
    assert server.opened[0][1].topic == "joystick plan"
    assert server.sent[0][0] == NEW and hub.state.held == {}


async def test_a_steer_guess_they_redirect_opens_its_lane(tmp_path, trusted) -> None:
    # Nothing goes into the working lane before they choose: their "new"
    # opens the lane the steer guess would have skipped.
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Also check the logs.", "place": "steer",'
        ' "topic": "log check"}',
    )
    hub.submit("olha os logs também")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))

    entry = await hub.correct_place(hub.state.entries[-1].place["id"], "new", via="said")
    await hub.close()

    assert entry is not None and entry.session_id == NEW
    assert len(server.sent) == 1 and server.sent[0][0] == NEW
    assert hub.state.held == {}


async def test_a_lane_opened_by_mistake_is_closed_and_the_message_goes_to_the_busy_lane(
    tmp_path, trusted
) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Is it using the old scene?", "place": "new",'
        ' "topic": "scene question"}',
        '{"correct_place": "steer", "say": ""}',
    )
    hub.submit("ele tá usando a cena antiga?")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    opened = await hub.correct_place(hub.state.entries[-1].place["id"], "new", via="button")
    assert opened is not None and opened.session_id == NEW
    # Said in the hub, not a button: it is about their newest placement.
    hub.submit("não, isso era pro que ele tá fazendo")
    await _settle(hub, until=lambda: len(server.sent) == 2)
    await hub.close()

    assert server.closed == [NEW]
    assert server.sent[1] == (
        SIM,
        _their_words("ele tá usando a cena antiga?", "Is it using the old scene?"),
    )
    assert hub.state.entries[-1].text == "Closed the new lane and sent this to amarras · sim."


async def test_an_old_lane_opened_by_mistake_is_left_open(tmp_path, trusted, monkeypatch) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Plan it.", "place": "new", "topic": "plan"}',
    )
    hub.submit("planeja")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    opened = await hub.correct_place(hub.state.entries[-1].place["id"], "new", via="button")
    assert opened is not None and opened.session_id == NEW
    monkeypatch.setattr(hub_place, "CLOSE_FRESH_LANE_S", -1.0)

    # Their newest placement is the lane their choice opened.
    entry = await hub.correct_latest_place("steer", via="button")
    await hub.close()

    assert server.closed == [] and len(server.sent) == 1
    assert entry is not None and "left it open" in entry.text


async def test_a_choice_for_a_busy_lane_teaches_the_router(tmp_path, trusted) -> None:
    _router, server, hub = _busy(
        tmp_path, '{"send": "5151", "message": "Stop.", "place": "steer"}'
    )
    hub.submit("para")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    place_id = hub.state.entries[-1].place["id"]

    entry = await hub.correct_place(place_id, "steer", via="button")
    assert entry is not None and entry.kind == "sent" and entry.place["picked"] == "steer"
    await hub.close()

    assert server.sent == [(SIM, _their_words("para", "Stop."))]
    assert hub.state.held == {}
    assert hub_place.find(tmp_path / "u1" / hub_place.LOG_NAME, place_id).right == "steer"
    assert (
        '"para": it belonged "steer" (the hub asked; it had guessed "steer").'
        in hub_place.lessons(tmp_path / "u1" / hub_place.LOG_NAME)
    )


async def test_the_decision_model_answers_the_same_question_in_shadow(
    tmp_path, monkeypatch, trusted
) -> None:
    async def post_questions(backend, state, questions):
        assert "## The busy lane\namarras · sim, working on this task:\ntune the MPC" in state
        return {"same_task": {"noul": 0.2}, "needs_result": {"noul": 0.1}}, {}

    monkeypatch.setattr(hub_place, "post_questions", post_questions)
    monkeypatch.setattr(model_router, "configured", lambda hub_dir: object())
    _router, _server, hub = _busy(
        tmp_path, '{"send": "5151", "message": "Plan it.", "place": "steer"}'
    )
    hub._judge_tiers = True
    hub.submit("planeja o joystick")
    # Confidence mode is off, so nothing is sent: the placement is still
    # logged and judged in shadow.
    await _settle(hub, until=lambda: len(_placements(tmp_path)) == 2)
    await hub.close()

    assert _server.sent == []

    shadow = _placements(tmp_path)[1]
    assert shadow["shadow"] == _placements(tmp_path)[0]["placement"]["id"]
    assert (shadow["pick"], shadow["p_same_task"]) == ("new", 0.2)
    record = hub_place.read_log(tmp_path / "u1" / hub_place.LOG_NAME)[shadow["shadow"]]
    assert record.shadow["pick"] == "new"


def test_the_hub_chat_shows_where_a_message_went_beside_its_chip() -> None:
    entry = HubEntry(
        id=3,
        at=time.time(),
        role="hub",
        kind="held",
        text="Vai quando terminar.",
        session_id=SIM,
        session_name="RAL",
        detail="Then plot it.",
        place={"id": "ab12", "picked": "after"},
    )
    shown = hub_chat.render(entry, "http://test")
    assert shown.startswith(
        f'[RAL](http://test/c/{SIM} "omnigent-project") '
        '[after it finishes](http://test/hub/placements/ab12?picked=after "omnigent-hub-place")'
    )
    # Never read aloud: speech is the hub's own words.
    assert hub_chat.speech_text(entry) == "Vai quando terminar."


async def test_a_correction_from_the_page_goes_through_the_route(
    tmp_path, trusted, monkeypatch
) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from omnigent.server.routes import hub as hub_routes

    _router, server, hub = _busy(
        tmp_path, '{"send": "5151", "message": "Then plot.", "place": "after"}'
    )
    hub.submit("depois plota")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    place_id = hub.state.entries[-1].place["id"]

    # The route's own hub, without the stores and conversation behind it.
    monkeypatch.setattr(hub_chat, "ensure_hub_conversation", lambda user_id, **kw: "hubchat")
    monkeypatch.setattr(hub_module, "hub_for", lambda user_id: hub)
    monkeypatch.setattr(hub_chat, "attach", lambda *a, **kw: None)
    monkeypatch.setattr(hub_chat, "bind_request", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(
        hub_routes.create_hub_router(conversation_store=object(), artifact_store=object()),
        prefix="/v1",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        bad = await client.post(f"/v1/hub/placements/{place_id}/feedback", json={"right": "x"})
        missing = await client.post("/v1/hub/placements/nope/feedback", json={"right": "new"})
        fixed = await client.post(
            f"/v1/hub/placements/{place_id}/feedback", json={"right": "steer"}
        )
    await hub.close()

    assert bad.status_code == 422 and missing.status_code == 404
    assert fixed.json()["right"] == "steer"
    assert server.sent == [(SIM, _their_words("depois plota", "Then plot."))]
    assert hub_module.HubState("u1", root=tmp_path).held == {}


# -- asking them, until the router has learned ---------------------------------------


async def test_until_the_router_has_learned_the_hub_asks_and_the_message_waits(tmp_path) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Plan the joystick.", "place": "new", "topic": "joystick",'
        ' "say": "Abri uma lane."}',
    )
    hub.submit("planeja o joystick")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    asked = hub.state.entries[-1]
    assert server.sent == [] and server.opened == []
    assert asked.place == {"id": asked.place["id"], "picked": "ask", "guess": "new"}
    # Not the router's words: they described a lane that was not opened.
    assert asked.text.startswith("Where should this go: into amarras · sim's work now")
    assert hub.state.held[SIM]["items"][0]["place"] == asked.place["id"]
    shown = hub_chat.render(asked, "http://test")
    assert f"/hub/placements/{asked.place['id']}?picked=ask&guess=new" in shown


@pytest.mark.parametrize(
    ("right", "sent_to", "still_held"),
    [("steer", SIM, False), ("new", NEW, False), ("after", None, True)],
)
async def test_their_answer_moves_the_message_and_teaches_the_router(
    tmp_path, right, sent_to, still_held
) -> None:
    router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Use the right bench.", "place": "steer",'
        ' "topic": "right bench"}',
        '{"answer": "ok"}',
    )
    hub.submit("usa só a bancada da direita")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))

    entry = await hub.correct_place(hub.state.entries[-1].place["id"], right, via="button")
    hub.submit("oi")
    await _settle(hub, until=lambda: len(router.prompts) == 2)
    await hub.close()

    assert entry is not None
    assert [to for to, _ in server.sent] == ([sent_to] if sent_to else [])
    assert bool(hub.state.held) is still_held
    if right == "after":
        assert entry.text == "Ok: it goes to amarras · sim when it finishes."
    assert (
        f'"usa só a bancada da direita": it belonged "{right}" '
        '(the hub asked; it had guessed "steer").'
    ) in router.prompts[1]


async def test_a_router_that_cannot_tell_asks_even_once_trusted(tmp_path, trusted) -> None:
    _router, _server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Check the scene.", "place": "ask",'
        ' "say": "Isso é pro que o sim tá fazendo ou uma lane nova?"}',
    )
    hub.submit("e a cena?")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    asked = hub.state.entries[-1]
    assert (asked.place["picked"], asked.place["guess"]) == ("ask", "")
    assert asked.text == "Isso é pro que o sim tá fazendo ou uma lane nova?"


async def test_a_place_it_has_not_earned_is_asked_even_when_steer_is_trusted(tmp_path) -> None:
    log = tmp_path / "u1" / hub_place.LOG_NAME
    for n in range(10):
        _answered(log, n, "steer", "steer")
    _router, _server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Look at the other dashboard.", "place": "new"}',
    )
    hub.submit("e o outro dashboard?")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    asked = hub.state.entries[-1]
    assert (asked.place["picked"], asked.place["guess"]) == ("ask", "new")


def _answered(path, n: int, guess: str, right: str) -> None:
    placement = hub_place.Placement(
        id=f"p{n}",
        at=1000.0 + n,
        project="RAL",
        project_id="r",
        lane="RAL",
        lane_id="l",
        task="t",
        message="m",
        said="s",
        picked="ask",
        guess=guess,
    )
    hub_place.log_placement(path, placement)
    hub_place.log_feedback(path, placement.id, right, via="button")


def test_the_router_decides_alone_once_nine_of_its_last_ten_guesses_matched(tmp_path) -> None:
    path = tmp_path / hub_place.LOG_NAME
    for n in range(9):
        _answered(path, n, "steer", "steer")
    assert not hub_place.trusted(path, "steer")  # nine answers are not enough
    _answered(path, 9, "steer", "steer")
    assert hub_place.trusted(path, "steer")  # 10 of 10 matched
    _answered(path, 10, "steer", "new")
    assert hub_place.trusted(path, "steer")  # 9 of the newest 10
    _answered(path, 11, "steer", "new")
    assert not hub_place.trusted(path, "steer")  # 8 of the newest 10


def test_each_place_is_trusted_on_its_own_guesses(tmp_path) -> None:
    path = tmp_path / hub_place.LOG_NAME
    for n in range(10):
        _answered(path, n, "steer", "steer")
    assert hub_place.trusted(path, "steer")
    # Ten right "steer" guesses say nothing about "new" or "after", or an empty guess.
    assert not hub_place.trusted(path, "new")
    assert not hub_place.trusted(path, "after")
    assert not hub_place.trusted(path, "")
    assert not hub_place.trusted(path, hub_place.ASK)


async def test_tell_it_now_says_the_held_messages_belonged_in_its_work(tmp_path) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Use the right bench.", "place": "after"}',
        '{"send": "5151", "message": "", "now": true}',
    )
    hub.submit("usa a bancada da direita")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    place_id = hub.state.entries[-1].place["id"]
    hub.submit("fala agora")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    assert server.sent == [(SIM, _their_words("usa a bancada da direita", "Use the right bench."))]
    assert hub_place.find(tmp_path / "u1" / hub_place.LOG_NAME, place_id).right == "steer"
