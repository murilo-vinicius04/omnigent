"""Messages for a busy lane: placed by the router, corrected by them, kept as lessons."""

from __future__ import annotations

import json
import time

from omnigent.server import hub as hub_module
from omnigent.server import hub_chat, hub_place, model_router
from omnigent.server.hub import HubEntry, TurnState, parse_decision
from tests.server.test_hub import _hub, _Router, _settle
from tests.server.test_hub_lanes import NEW, SIM, _LaneServer, _two_lanes


def _placements(tmp_path) -> list[dict]:
    path = tmp_path / "u1" / hub_place.LOG_NAME
    lines = path.read_text().splitlines() if path.exists() else []
    return [json.loads(line) for line in lines]


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


async def test_a_direction_for_the_busy_lane_goes_into_its_work_now(tmp_path) -> None:
    router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Pause the tuning; the lanes come first.", "place": "steer",'
        ' "say": "Entrou agora no que ele está fazendo."}',
    )
    hub.submit("fala pra ele parar o tuning e focar nas lanes")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    assert server.sent == [(SIM, "Pause the tuning; the lanes come first.")]
    assert hub.state.held == {}
    sent = hub.state.entries[-1]
    assert sent.kind == "sent" and sent.place["picked"] == "steer"
    (row,) = _placements(tmp_path)
    assert row["placement"]["id"] == sent.place["id"]
    assert row["placement"]["task"] == "tune the MPC gains"
    assert row["placement"]["said"] == "fala pra ele parar o tuning e focar nas lanes"
    # The router reads where steering, waiting and new lanes apply.
    assert '"steer" -- it is about the task the lane is doing now' in router.prompts[0]


async def test_a_next_step_waits_for_the_busy_lane(tmp_path) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Then plot the gains.", "place": "after",'
        ' "say": "Vai quando terminar."}',
    )
    hub.submit("quando terminar plota os ganhos")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    assert server.sent == []
    held = hub.state.entries[-1]
    assert held.kind == "held" and held.place["picked"] == "after"
    assert hub.state.held[SIM]["items"][0]["place"] == held.place["id"]


async def test_a_different_task_opens_a_new_lane(tmp_path) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Plan the joystick experiment.", "place": "new",'
        ' "topic": "joystick plan", "say": "Abri uma lane nova."}',
    )
    hub.submit("monta um plano de experimento pro joystick")
    await _settle(hub, until=lambda: server.sent)
    await hub.close()

    ((_, spec),) = server.opened
    assert spec.topic == "joystick plan"
    ((to, text),) = server.sent
    assert to == NEW and text.startswith("Plan the joystick experiment.")
    sent = hub.state.entries[-1]
    assert sent.kind == "sent" and sent.place["picked"] == "new"
    assert _placements(tmp_path)[0]["placement"]["opened"] == NEW


async def test_without_a_place_a_message_for_a_busy_lane_waits(tmp_path) -> None:
    _router, server, hub = _busy(tmp_path, '{"send": "5151", "message": "Log them."}')
    hub.submit("loga os ganhos")
    await _settle(hub, until=lambda: any(e.kind == "held" for e in hub.state.entries))
    await hub.close()

    assert server.sent == []
    assert _placements(tmp_path)[0]["placement"]["by"] == "default"


async def test_a_held_message_they_correct_goes_in_now_and_becomes_a_lesson(tmp_path) -> None:
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
    assert server.sent == [(SIM, "Use the right bench only.")]
    assert hub.state.held == {}

    # From then on the router is shown the correction.
    hub.submit("oi")
    await _settle(hub, until=lambda: len(router.prompts) == 2)
    await hub.close()
    assert '"usa só a bancada da direita": it belonged "steer", not "after".' in router.prompts[1]
    assert _placements(tmp_path)[-1] == {
        "feedback": place_id,
        "right": "steer",
        "via": "button",
        "at": _placements(tmp_path)[-1]["at"],
    }


async def test_a_held_message_that_was_a_new_task_opens_its_lane(tmp_path) -> None:
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


async def test_a_message_already_in_a_lanes_work_stays_there(tmp_path) -> None:
    _router, server, hub = _busy(
        tmp_path, '{"send": "5151", "message": "Also check the logs.", "place": "steer"}'
    )
    hub.submit("olha os logs também")
    await _settle(hub, until=lambda: server.sent)

    entry = await hub.correct_place(hub.state.entries[-1].place["id"], "new", via="said")
    await hub.close()

    assert entry is not None and entry.kind == "attention"
    assert "already gone into amarras · sim's work" in entry.text
    assert len(server.sent) == 1 and server.opened == []


async def test_a_lane_opened_by_mistake_is_closed_and_the_message_goes_to_the_busy_lane(
    tmp_path,
) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Is it using the old scene?", "place": "new",'
        ' "topic": "scene question"}',
        '{"correct_place": "steer", "say": ""}',
    )
    hub.submit("ele tá usando a cena antiga?")
    await _settle(hub, until=lambda: server.sent)
    # Said in the hub, not a button: it is about their newest placement.
    hub.submit("não, isso era pro que ele tá fazendo")
    await _settle(hub, until=lambda: len(server.sent) == 2)
    await hub.close()

    assert server.closed == [NEW]
    assert server.sent[1] == (SIM, "Is it using the old scene?")
    assert hub.state.entries[-1].text == "Closed the new lane and sent this to amarras · sim."


async def test_an_old_lane_opened_by_mistake_is_left_open(tmp_path, monkeypatch) -> None:
    _router, server, hub = _busy(
        tmp_path,
        '{"send": "5151", "message": "Plan it.", "place": "new", "topic": "plan"}',
    )
    hub.submit("planeja")
    await _settle(hub, until=lambda: server.sent)
    monkeypatch.setattr(hub_place, "CLOSE_FRESH_LANE_S", -1.0)

    entry = await hub.correct_place(hub.state.entries[-1].place["id"], "steer", via="button")
    await hub.close()

    assert server.closed == [] and len(server.sent) == 1
    assert entry is not None and "left it open" in entry.text


async def test_a_right_placement_is_kept_as_a_label_but_not_a_lesson(tmp_path) -> None:
    _router, server, hub = _busy(
        tmp_path, '{"send": "5151", "message": "Stop.", "place": "steer"}'
    )
    hub.submit("para")
    await _settle(hub, until=lambda: server.sent)
    place_id = hub.state.entries[-1].place["id"]
    shown = len(hub.state.entries)

    assert await hub.correct_place(place_id, "steer", via="button") is None
    await hub.close()

    assert len(hub.state.entries) == shown
    assert hub_place.find(tmp_path / "u1" / hub_place.LOG_NAME, place_id).right == "steer"
    assert hub_place.lessons(tmp_path / "u1" / hub_place.LOG_NAME) == ""


async def test_the_decision_model_answers_the_same_question_in_shadow(
    tmp_path, monkeypatch
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
    await _settle(hub, until=lambda: len(_placements(tmp_path)) == 2)
    await hub.close()

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


async def test_a_correction_from_the_page_goes_through_the_route(tmp_path, monkeypatch) -> None:
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
    assert server.sent == [(SIM, "Then plot.")]
    assert hub_module.HubState("u1", root=tmp_path).held == {}
