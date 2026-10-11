"""The hub as a regular conversation: made on first use, typed into, answered in place."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from omnigent.server import discussion, hub_chat
from omnigent.server import hub as hub_module
from omnigent.server.hub import HubEntry


@pytest.fixture()
def _hub_home(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path / "hub")
    monkeypatch.setattr(hub_module, "_HUBS", {})
    monkeypatch.setattr(discussion, "LEDGER_DIR", tmp_path / "ledgers")
    replies: list[str] = []

    async def decide(self, prompt, *, said, timeout_s):
        replies.append(said)
        return '{"answer": "Oi! Nenhuma sessão por aqui ainda."}'

    monkeypatch.setattr(hub_module.HubRouter, "decide", decide)
    return replies


async def _items(client: httpx.AsyncClient, session_id: str) -> list[dict]:
    """The session's stored items, flattened the way the items route serves them."""
    res = await client.get(f"/v1/sessions/{session_id}/items", params={"limit": 50})
    assert res.status_code == 200, res.text
    return [{**i.get("data", {}), **i} for i in res.json()["data"]]


async def test_the_hub_is_one_pinned_conversation_made_on_first_use(client, _hub_home) -> None:
    first = await client.get("/v1/hub")
    assert first.status_code == 200, first.text
    session_id = first.json()["session_id"]
    assert (await client.get("/v1/hub")).json()["session_id"] == session_id

    session = await client.get(f"/v1/sessions/{session_id}")
    assert session.status_code == 200, session.text
    body = session.json()
    assert body["title"] == "Hub"
    assert body["labels"]["omnigent.hub"] == "1"
    assert "omnigent.pinned" in body["labels"]
    listed = await client.get("/v1/sessions", params={"pinned": "true"})
    assert session_id in [s["id"] for s in listed.json()["data"]]


async def test_a_message_typed_into_the_hub_is_answered_there(client, _hub_home) -> None:
    session_id = (await client.get("/v1/hub")).json()["session_id"]
    posted = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "oi"}]},
        },
    )
    # Taken by the hub: no runner is bound to it, and none is needed.
    assert posted.status_code == 202, posted.text
    assert posted.json()["queued"] is True
    for _ in range(100):
        items = await _items(client, session_id)
        if any(i.get("role") == "assistant" for i in items):
            break
        await asyncio.sleep(0.02)
    said = [(i["role"], i["content"][0]["text"]) for i in items if i["type"] == "message"]
    assert said == [("user", "oi"), ("assistant", "Oi! Nenhuma sessão por aqui ainda.")]
    assert _hub_home == ["oi"]


async def test_a_deleted_hub_is_made_again(client, _hub_home) -> None:
    session_id = (await client.get("/v1/hub")).json()["session_id"]
    assert (await client.delete(f"/v1/sessions/{session_id}")).status_code in (200, 204)
    again = (await client.get("/v1/hub")).json()["session_id"]
    assert again != session_id


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param(
            HubEntry(id=1, at=0, role="hub", kind="message", text="Oi!"),
            "Oi!",
            id="plain",
        ),
        pytest.param(
            HubEntry(
                id=2,
                at=0,
                role="hub",
                kind="sent",
                text="Mandei pra brasa.",
                session_id="s1",
                session_name="brasa",
                detail="Run the tests.\nThen report.",
            ),
            "Mandei pra brasa.\n\n> Run the tests.\n> Then report.\n\n"
            "[Open brasa ↗](http://h:6767/c/s1)",
            id="sent-quotes-what-went",
        ),
        pytest.param(
            HubEntry(
                id=3,
                at=0,
                role="hub",
                kind="attention",
                text="amarras is waiting for your approval.",
                session_id="s2",
                session_name="amarras",
            ),
            "amarras is waiting for your approval.\n\n[Open amarras ↗](http://h:6767/c/s2)",
            id="attention-links-the-session",
        ),
    ],
)
def test_hub_entries_render_as_chat_markdown(entry, expected) -> None:
    assert hub_chat.render(entry, "http://h:6767") == expected


def test_a_long_reply_is_cut_and_points_at_its_session() -> None:
    entry = HubEntry(
        id=4,
        at=0,
        role="hub",
        kind="finished",
        text="brasa finished: all good.",
        session_id="s1",
        session_name="brasa",
        detail="x" * 5000,
    )
    text = hub_chat.render(entry, "")
    assert text.startswith("brasa finished: all good.\n\n> xxx")
    assert "… (the rest is in brasa)" in text
    assert "Open brasa" not in text  # no address known, so no link


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"origin": "http://localhost:6767"}, "http://localhost:6767"),
        ({"host": "100.1.2.3:6767"}, "http://100.1.2.3:6767"),
        ({"host": "hub.example", "x-forwarded-proto": "https"}, "https://hub.example"),
        ({}, ""),
    ],
)
def test_links_use_the_address_the_reader_opened(headers, expected) -> None:
    assert hub_chat.base_url(headers) == expected


async def test_a_voice_hands_the_hub_what_was_said_and_gets_what_to_say(client, _hub_home) -> None:
    session_id = (await client.get("/v1/hub")).json()["session_id"]
    res = await client.post(
        "/v1/hub/voice",
        json={"text": "tudo certo por aí?", "context": "Me: oi\nVoice: Oi!"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["say"] == "Oi! Nenhuma sessão por aqui ainda."
    # What was said on the call, and the hub's answer, show in the hub too.
    items = await _items(client, session_id)
    said = [(i["role"], i["content"][0]["text"]) for i in items if i["type"] == "message"]
    assert said == [
        ("user", "🎙 tudo certo por aí?"),
        ("assistant", "Oi! Nenhuma sessão por aqui ainda."),
    ]
    assert _hub_home == ["tudo certo por aí?"]


async def test_the_call_hears_only_news_from_after_it_opened(client, _hub_home) -> None:
    await client.get("/v1/hub")
    hub = hub_module._HUBS["local"]
    hub.say("brasa finished: old news.", kind="finished", session_id="s1", session_name="brasa")
    opened_at = (await client.get("/v1/hub")).json()["last_id"]
    hub.say("Sent to amarras.", kind="sent", session_id="s2", session_name="amarras")
    hub.say("amarras finished: green.", kind="finished", session_id="s2", session_name="amarras")
    res = await client.get("/v1/hub/updates", params={"after": opened_at, "wait": 0})
    body = res.json()
    assert [u["text"] for u in body["updates"]] == ["amarras finished: green."]
    assert body["last_id"] == opened_at + 2
    # Nothing new: the long-poll returns empty when its wait runs out.
    quiet = await client.get("/v1/hub/updates", params={"after": body["last_id"], "wait": 0.05})
    assert quiet.json() == {"updates": [], "last_id": body["last_id"]}


async def test_the_hub_reads_and_changes_a_session_model_through_its_own_routes(
    client, _hub_home
) -> None:
    from tests.server.helpers import create_test_session

    session = await create_test_session(
        client,
        title="Amarras",
        labels={"omnigent.wrapper": "claude-code-native-ui", "omnigent.ui": "terminal"},
    )
    listed = (await client.get("/v1/hub/sessions")).json()["sessions"]
    run = next(s["run"] for s in listed if s["id"] == session["id"])
    assert run["provider"] == "Claude Code"
    assert run["efforts"] == ["low", "medium", "high", "xhigh", "max"]

    backend = hub_module.hub_for(None).require_backend()
    await backend.configure(session["id"], "sonnet", "high")

    stored = (await client.get(f"/v1/sessions/{session['id']}")).json()
    assert (stored["model_override"], stored["reasoning_effort"]) == ("sonnet", "high")
    listed = (await client.get("/v1/hub/sessions")).json()["sessions"]
    run = next(s["run"] for s in listed if s["id"] == session["id"])
    # What it reports running stays the truth until it reports the switch.
    assert (run["model"], run["requested"], run["effort"]) == ("test-agent", "sonnet", "high")


async def test_a_pick_in_the_sessions_panel_is_checked_and_logged_in_the_hub(
    client, _hub_home
) -> None:
    from tests.server.helpers import create_test_session

    session = await create_test_session(
        client, title="Amarras", labels={"omnigent.wrapper": "claude-code-native-ui"}
    )
    hub_id = (await client.get("/v1/hub")).json()["session_id"]

    refused = await client.post(
        "/v1/hub/model", json={"session_id": session["id"], "effort": "ultra"}
    )
    assert refused.status_code == 200, refused.text
    assert refused.json()["changed"] is False
    assert "can't take 'ultra' effort" in refused.json()["text"]

    done = await client.post("/v1/hub/model", json={"session_id": session["id"], "effort": "max"})
    assert done.json() == {
        "changed": True,
        "text": "Switched Amarras to max effort; it applies from its next message.",
    }
    stored = (await client.get(f"/v1/sessions/{session['id']}")).json()
    assert stored["reasoning_effort"] == "max"
    said = [
        i["content"][0]["text"] for i in await _items(client, hub_id) if i["type"] == "message"
    ]
    assert said[-1].startswith("Switched Amarras to max effort;")

    missing = await client.post("/v1/hub/model", json={"session_id": "nope", "effort": "max"})
    assert missing.status_code == 404


async def test_sessions_are_added_removed_and_renamed_from_the_panel(client, _hub_home) -> None:
    from tests.server.helpers import create_test_session

    amarras = await create_test_session(client, title="Project Analysis")
    greeting = await create_test_session(client, title="Greeting")
    hub_id = (await client.get("/v1/hub")).json()["session_id"]

    async def in_hub() -> list[tuple[str, str]]:
        listed = (await client.get("/v1/hub/sessions")).json()["sessions"]
        return sorted((s["id"], s["name"]) for s in listed)

    assert await in_hub() == sorted(
        [(amarras["id"], "Project Analysis"), (greeting["id"], "Greeting")]
    )

    removed = await client.delete(f"/v1/hub/members/{greeting['id']}")
    assert removed.status_code == 200, removed.text
    assert await in_hub() == [(amarras["id"], "Project Analysis")]
    later = await create_test_session(client, title="Started later")
    assert await in_hub() == [(amarras["id"], "Project Analysis")]
    offered = [c["id"] for c in (await client.get("/v1/hub/candidates")).json()["sessions"]]
    assert offered == [later["id"], greeting["id"]]  # never the hub itself

    added = await client.post("/v1/hub/members", json={"session_id": greeting["id"]})
    assert added.json() == {"text": "Added Greeting to the hub."}
    assert (await client.post("/v1/hub/members", json={"session_id": hub_id})).status_code == 404

    renamed = await client.patch(f"/v1/hub/members/{amarras['id']}", json={"name": "amarras"})
    assert renamed.json() == {"text": "Renamed Project Analysis to amarras."}
    clash = await client.patch(f"/v1/hub/members/{greeting['id']}", json={"name": "Amarras"})
    assert clash.status_code == 409
    assert clash.json()["detail"] == "amarras is already called that"
    assert await in_hub() == sorted([(amarras["id"], "amarras"), (greeting["id"], "Greeting")])
    gone = await client.patch(f"/v1/hub/members/{later['id']}", json={"name": "x"})
    assert gone.status_code == 404

    said = [
        i["content"][0]["text"] for i in await _items(client, hub_id) if i["type"] == "message"
    ]
    assert said[-3].startswith("Took Greeting out of the hub;")
    assert said[-1].startswith("Renamed Project Analysis to amarras.")
