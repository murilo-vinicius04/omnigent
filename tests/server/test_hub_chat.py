"""The hub as a regular conversation: made on first use, typed into, answered in place."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server import discussion, hub_chat
from omnigent.server import hub as hub_module
from omnigent.server.app import create_app
from omnigent.server.hub import HubEntry
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.comment_store.sqlalchemy_store import SqlAlchemyCommentStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.project_store.sqlalchemy_store import SqlAlchemyProjectStore


@pytest.fixture()
def app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """The test app with first-class projects, which the hub runs."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
        comment_store=SqlAlchemyCommentStore(db_uri),
        project_store=SqlAlchemyProjectStore(db_uri),
    )


@pytest.fixture()
def _hub_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIGENT_HUB_MODEL", "test-hub-model")  # never asks the real agy
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


def _kind(item: dict) -> str:
    """The type of a stored message's first part, e.g. ``"spoken_summary"``."""
    return (item.get("content") or [{}])[0].get("type", "")


async def test_the_hubs_messages_are_read_aloud_and_still_shown_as_written(
    client, _hub_home, db_uri, tmp_path, monkeypatch
) -> None:
    from omnigent.server import tts

    monkeypatch.setenv("OMNIGENT_SPOKEN_SUMMARY_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SPOKEN_SUMMARY_LANGUAGE", "pt-BR")
    recorded: list[tuple[str, str]] = []

    async def synthesize(text: str, *, language: str = "pt-BR") -> tts.SummaryAudio:
        recorded.append((text, language))
        return tts.SummaryAudio(b"ID3voice", "summary.mp3", "audio/mpeg")

    monkeypatch.setattr(tts, "synthesize_summary", synthesize)
    monkeypatch.setattr(
        hub_module,
        "_file_stores",
        (SqlAlchemyFileStore(db_uri), LocalArtifactStore(str(tmp_path / "artifacts"))),
    )
    session_id = (await client.get("/v1/hub")).json()["session_id"]
    await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "oi"}]},
        },
    )
    for _ in range(200):
        items = await _items(client, session_id)
        spoken = [i for i in items if _kind(i) == "spoken_summary"]
        if len(spoken) == 2:
            break
        await asyncio.sleep(0.02)

    [reply] = [i for i in items if i["role"] == "assistant" and _kind(i) == "output_text"]
    assert reply["content"] == [
        {"type": "output_text", "text": "Oi! Nenhuma sessão por aqui ainda."}
    ]
    # The words first, then the same words with their recording, both on the reply's turn.
    words, voiced = (i["content"][0] for i in spoken)
    assert [i["response_id"] for i in spoken] == [reply["response_id"]] * 2
    assert words == {
        "type": "spoken_summary",
        "text": "Oi! Nenhuma sessão por aqui ainda.",
        "lang": "pt-BR",
        "speak_only": True,
        "audio_pending": True,
    }
    assert voiced["speak_only"] is True and voiced["text"] == words["text"]
    assert recorded == [("Oi! Nenhuma sessão por aqui ainda.", "pt-BR")]
    audio = await client.get(
        f"/v1/sessions/{session_id}/resources/files/{voiced['audio_file_id']}/content"
    )
    assert audio.content == b"ID3voice"


async def test_without_spoken_summaries_the_hub_stays_silent(client, _hub_home) -> None:
    session_id = (await client.get("/v1/hub")).json()["session_id"]
    await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "oi"}]},
        },
    )
    for _ in range(100):
        items = await _items(client, session_id)
        if any(i.get("role") == "assistant" for i in items):
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    items = await _items(client, session_id)
    assert not [i for i in items if _kind(i) == "spoken_summary"]


def test_the_hub_says_its_own_words_never_what_it_quotes_or_shows(tmp_path) -> None:
    sent = HubEntry(
        id=2,
        at=0,
        role="hub",
        kind="sent",
        text="Mandei pra **brasa** rodar `npm test`.",
        session_id="s1",
        session_name="brasa",
        detail="Run the tests.\nThen report.",
    )
    assert hub_chat.speech_text(sent) == "Mandei pra brasa rodar npm test."
    report = _finished(tmp_path, text="RAL finished: o plano está em `REVISION_PLAN.md`.")
    assert hub_chat.speech_text(report) == "RAL finished: o plano está em REVISION_PLAN.md."


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


def _finished(tmp_path, **fields) -> HubEntry:
    (tmp_path / "RA-L" / "reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "RA-L" / "reviews" / "REVISION_PLAN.md").write_text("# plan\n")
    base = {
        "id": 5,
        "at": 0,
        "role": "hub",
        "kind": "finished",
        "text": "RAL finished: o plano está pronto.",
        "session_id": "s1",
        "session_name": "RAL",
        "workspace": str(tmp_path),
        "detail": (
            "I've updated the plan. The full v2 plan is in `RA-L/reviews/REVISION_PLAN.md`.\n\n"
            "| Group | Conditions |\n|---|---|\n| Ours | A1, A2 |\n\n"
            "Not files: `/etc/hosts`, `npm test`, `missing/notes.md`.\n"
            "```\n| not | a table |\n|---|---|\ncat RA-L/reviews/REVISION_PLAN.md\n```\n"
        ),
    }
    return HubEntry(**{**base, **fields})


PLAN = "http://h:6767/c/s1?file=RA-L/reviews/REVISION_PLAN.md"


def test_a_report_shows_what_the_projects_summary_shows_never_the_reply(tmp_path) -> None:
    table = "| Group | Conditions |\n|---|---|\n| Ours | A1, A2 |"
    entry = _finished(tmp_path, show=[{"kind": "table", "label": "table", "content": table}])
    assert hub_chat.render(entry, "http://h:6767") == (
        "RAL finished: o plano está pronto.\n\n"
        f"{table}\n\n"
        f"[`RA-L/reviews/REVISION_PLAN.md`]({PLAN})\n\n"
        "[Open RAL ↗](http://h:6767/c/s1)"
    )


def test_without_a_summary_the_report_lifts_only_the_replys_tables(tmp_path) -> None:
    text = hub_chat.render(_finished(tmp_path), "http://h:6767")
    assert "| Group | Conditions |\n|---|---|\n| Ours | A1, A2 |" in text
    assert "I've updated the plan" not in text and "| not | a table |" not in text
    assert f"[`RA-L/reviews/REVISION_PLAN.md`]({PLAN})" in text


def test_without_the_apps_address_a_report_makes_no_links(tmp_path) -> None:
    text = hub_chat.render(_finished(tmp_path), "")
    assert "](http" not in text and "?file=" not in text
    assert text.startswith("RAL finished: o plano está pronto.\n\n| Group |")


def test_the_hub_links_a_file_a_project_named_wherever_it_mentions_it(tmp_path) -> None:
    report = _finished(tmp_path, workspace=None)  # written before reports kept the folder
    entries = [report, HubEntry(id=6, at=0, role="hub", kind="message", text="x")]
    files = hub_chat.known_files(entries, lambda sid: str(tmp_path) if sid == "s1" else None)
    assert files["REVISION_PLAN.md"] == ("s1", "RA-L/reviews/REVISION_PLAN.md")
    said = HubEntry(
        id=7,
        at=0,
        role="hub",
        kind="message",
        text=(
            "O RAL terminou a v2 em `REVISION_PLAN.md` (RA-L/reviews/REVISION_PLAN.md). "
            "Nada em `notes.md` nem [aqui](https://x.dev/REVISION_PLAN.md)."
        ),
    )
    assert hub_chat.render(said, "http://h:6767", files) == (
        f"O RAL terminou a v2 em [`REVISION_PLAN.md`]({PLAN}) "
        f"([RA-L/reviews/REVISION_PLAN.md]({PLAN})). "
        "Nada em `notes.md` nem [aqui](https://x.dev/REVISION_PLAN.md)."
    )
    assert hub_chat.render(said, "", files) == said.text  # no address, no links


def test_the_hubs_chat_reads_the_hubs_log_for_named_files(tmp_path) -> None:
    hub = hub_module.Hub("u1", router_factory=None, state=hub_module.HubState("u1", root=tmp_path))
    hub_chat.attach(hub, conversation_store=None, session_id="hub0")
    assert hub.chat.state is hub.state


def test_show_blocks_render_as_the_chat_draws_them(tmp_path) -> None:
    (tmp_path / "plots").mkdir()
    (tmp_path / "plots" / "speed.png").write_bytes(b"png")
    blocks = [
        {"kind": "output", "label": "pytest", "content": "17 passed"},
        {"kind": "link", "label": "the PR", "content": "https://github.com/o/r/pull/7"},
        {"kind": "image", "label": "diagram", "content": "https://x.dev/d.png"},
        {"kind": "image", "label": "speed", "content": str(tmp_path / "plots" / "speed.png")},
        {"kind": "image", "label": "chart", "content": "/v1/sessions/s1/resources/files/f1"},
    ]
    shown = hub_chat.show_markdown(blocks, str(tmp_path), lambda rel: f"u/{rel}")
    assert shown == (
        "```\n17 passed\n```\n\n"
        "[the PR](https://github.com/o/r/pull/7)\n\n"
        "![diagram](https://x.dev/d.png)\n\n"
        "[speed](u/plots/speed.png)"
    )  # the stored chart travels as a copied file instead


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
    assert res.json()["sent"] is False  # answered, nothing sent: the call stays open
    # What was said on the call, and the hub's answer, show in the hub too.
    items = await _items(client, session_id)
    said = [(i["role"], i["content"][0]["text"]) for i in items if i["type"] == "message"]
    assert said == [
        ("user", "🎙 tudo certo por aí?"),
        ("assistant", "Oi! Nenhuma sessão por aqui ainda."),
    ]
    assert _hub_home == ["tudo certo por aí?"]


async def test_a_voice_is_told_when_its_request_went_to_a_project(
    client, _hub_home, monkeypatch
) -> None:
    async def ask(self, text, *, via, context=""):
        return [HubEntry(id=9, at=0, role="hub", kind="sent", text="Mandei pro RAL.")]

    monkeypatch.setattr(hub_module.Hub, "ask", ask)
    await client.get("/v1/hub")
    res = await client.post("/v1/hub/voice", json={"text": "manda pro RAL", "context": ""})
    assert res.status_code == 200, res.text
    assert res.json()["say"] == "Mandei pro RAL."
    assert res.json()["sent"] is True  # the page ends the call, as after a hand-off to Claude


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


async def _project_with(client: httpx.AsyncClient, name: str, **session_args) -> dict:
    """A project made through the hub from a new chat of theirs; returns the chat."""
    from tests.server.helpers import create_test_session

    session = await create_test_session(client, **session_args)
    made = await client.post("/v1/hub/projects", json={"name": name, "from_chat": session["id"]})
    assert made.status_code == 200, made.text
    return {**session, "project_id": made.json()["id"]}


async def test_the_hub_reads_and_changes_a_project_model_through_its_own_routes(
    client, _hub_home
) -> None:
    session = await _project_with(
        client,
        "Amarras",
        title="Chains",
        labels={"omnigent.wrapper": "claude-code-native-ui", "omnigent.ui": "terminal"},
    )
    listed = (await client.get("/v1/hub/projects")).json()["projects"]
    run = next(p["chat"]["run"] for p in listed if p["chat"]["id"] == session["id"])
    assert run["provider"] == "Claude Code"
    assert run["efforts"] == ["low", "medium", "high", "xhigh", "max"]

    backend = hub_module.hub_for(None).require_backend()
    await backend.configure(session["id"], "sonnet", "high")

    stored = (await client.get(f"/v1/sessions/{session['id']}")).json()
    assert (stored["model_override"], stored["reasoning_effort"]) == ("sonnet", "high")
    listed = (await client.get("/v1/hub/projects")).json()["projects"]
    run = next(p["chat"]["run"] for p in listed if p["chat"]["id"] == session["id"])
    # What it reports running stays the truth until it reports the switch.
    assert (run["model"], run["requested"], run["effort"]) == ("test-agent", "sonnet", "high")


async def test_a_pick_in_the_projects_panel_is_checked_and_logged_in_the_hub(
    client, _hub_home
) -> None:
    session = await _project_with(
        client, "Amarras", title="Chains", labels={"omnigent.wrapper": "claude-code-native-ui"}
    )
    project_id = session["project_id"]
    hub_id = (await client.get("/v1/hub")).json()["session_id"]

    refused = await client.post(
        "/v1/hub/model", json={"project_id": project_id, "effort": "ultra"}
    )
    assert refused.status_code == 200, refused.text
    assert refused.json()["changed"] is False
    assert "can't take 'ultra' effort" in refused.json()["text"]

    done = await client.post("/v1/hub/model", json={"project_id": project_id, "effort": "max"})
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

    missing = await client.post("/v1/hub/model", json={"project_id": "nope", "effort": "max"})
    assert missing.status_code == 404


async def test_projects_are_made_renamed_given_chats_and_deleted_from_the_panel(
    client, _hub_home
) -> None:
    from tests.server.helpers import create_test_session

    chains = await create_test_session(client, title="Project Analysis")
    greeting = await create_test_session(client, title="Greeting")
    hub_id = (await client.get("/v1/hub")).json()["session_id"]

    async def projects() -> list[tuple[str, int, str | None]]:
        listed = (await client.get("/v1/hub/projects")).json()["projects"]
        return sorted((p["name"], p["chats"], (p["chat"] or {}).get("id")) for p in listed)

    # Nothing is a project until they make one; their chats are offered for one.
    assert await projects() == []
    offered = [c["id"] for c in (await client.get("/v1/hub/candidates")).json()["chats"]]
    assert offered == [greeting["id"], chains["id"]]  # never the hub itself

    empty = await client.post("/v1/hub/projects", json={"name": "robot arm"})
    assert empty.status_code == 200, empty.text
    assert empty.json()["link"] == "/?project=robot%20arm"
    made = await client.post(
        "/v1/hub/projects", json={"name": "amarras", "from_chat": chains["id"]}
    )
    assert made.json()["text"] == "Created project amarras, with “Project Analysis” as its chat."
    clash = await client.post("/v1/hub/projects", json={"name": "Amarras"})
    assert clash.status_code == 409
    assert clash.json()["detail"] == "there is already a project called amarras"
    assert await projects() == [("amarras", 1, chains["id"]), ("robot arm", 0, None)]
    # In the sidebar too: the chat is filed in the project.
    stored = (await client.get(f"/v1/sessions/{chains['id']}")).json()
    assert stored["project_id"] == made.json()["id"]

    arm = empty.json()["id"]
    added = await client.post(f"/v1/hub/projects/{arm}/chats", json={"session_id": greeting["id"]})
    assert added.json() == {"text": "Moved “Greeting” into robot arm."}
    taken = await client.post(f"/v1/hub/projects/{arm}/chats", json={"session_id": hub_id})
    assert taken.status_code == 404

    renamed = await client.patch(f"/v1/hub/projects/{arm}", json={"name": "arm"})
    assert renamed.json() == {"text": "Renamed robot arm to arm."}
    assert await projects() == [("amarras", 1, chains["id"]), ("arm", 1, greeting["id"])]

    deleted = await client.delete(f"/v1/hub/projects/{arm}")
    assert deleted.json() == {
        "text": "Deleted project arm; its chat is kept, outside any project."
    }
    assert await projects() == [("amarras", 1, chains["id"])]
    assert (await client.get(f"/v1/sessions/{greeting['id']}")).json().get("project_id") is None
    gone = await client.patch(f"/v1/hub/projects/{arm}", json={"name": "x"})
    assert gone.status_code == 404

    said = [
        i["content"][0]["text"] for i in await _items(client, hub_id) if i["type"] == "message"
    ]
    assert said[0].startswith("Created project robot arm. It has no chat yet")
    assert "[Start a chat in robot arm ↗](http://test/?project=robot%20arm)" in said[0]
    assert said[-1] == "Deleted project arm; its chat is kept, outside any project."


async def test_the_old_session_list_becomes_projects(client, _hub_home) -> None:
    from tests.server.helpers import create_test_session

    chains = await create_test_session(client, title="Project Analysis")
    loose = await create_test_session(client, title="Greeting")
    state = hub_module.HubState("local")
    state.set_members([chains["id"]])
    state.name(chains["id"], "amarras")
    listed = (await client.get("/v1/hub/projects")).json()["projects"]
    assert [(p["name"], p["chat"]["id"]) for p in listed] == [("amarras", chains["id"])]
    offered = [c["id"] for c in (await client.get("/v1/hub/candidates")).json()["chats"]]
    assert offered == [loose["id"]]


async def test_a_file_attached_in_the_hub_reaches_the_projects_chat(
    client, _hub_home, monkeypatch
) -> None:
    from tests.server.helpers import create_test_session

    hub_id = (await client.get("/v1/hub")).json()["session_id"]
    review = b"Reviewer 2: the related work misses two 2025 papers.\n"
    uploaded = await client.post(
        f"/v1/sessions/{hub_id}/resources/files",
        files={"file": ("Review541750(Reviewer2).txt", review, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    target = await create_test_session(client, title="RAL paper")

    # Test chats have no runner, so the message itself is caught here; the
    # file copy before it goes through the real routes.
    posted: list[dict] = []
    real_post = httpx.AsyncClient.post

    async def post(self, url, *args, **kwargs):
        if str(url).endswith(f"/v1/sessions/{target['id']}/events"):
            posted.append(kwargs["json"])
            return httpx.Response(202, request=httpx.Request("POST", str(url)))
        return await real_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    backend = hub_module.hub_for(None).require_backend()
    held = hub_module.attached_files(
        [{"type": "input_file", "file_id": file_id, "filename": "Review541750(Reviewer2).txt"}],
        hub_id,
    )
    await backend.send(target["id"], "Here are the reviews.", held)

    # The chat owns its own copy: a file id only resolves inside its own chat.
    files = (await client.get(f"/v1/sessions/{target['id']}/resources/files")).json()["data"]
    assert [f["name"] for f in files] == ["Review541750(Reviewer2).txt"]
    copy_id = files[0]["id"]
    assert copy_id != file_id
    content = await client.get(f"/v1/sessions/{target['id']}/resources/files/{copy_id}/content")
    assert content.content == review
    assert posted[0]["data"]["content"][:2] == [
        {"type": "input_file", "file_id": copy_id, "filename": "Review541750(Reviewer2).txt"},
        {"type": "input_text", "text": "Here are the reviews."},
    ]


async def test_files_typed_into_the_hub_are_held_for_the_next_send(client, _hub_home) -> None:
    hub_id = (await client.get("/v1/hub")).json()["session_id"]
    uploaded = await client.post(
        f"/v1/sessions/{hub_id}/resources/files",
        files={
            "file": ("ReportOn26-4238v1.txt", b"Decision: revise and resubmit.\n", "text/plain")
        },
    )
    file_id = uploaded.json()["id"]
    posted = await client.post(
        f"/v1/sessions/{hub_id}/events",
        json={
            "type": "message",
            "data": {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "file_id": file_id,
                        "filename": "ReportOn26-4238v1.txt",
                    },
                    {"type": "input_text", "text": "esses são os anexos"},
                ],
            },
        },
    )
    assert posted.status_code == 202, posted.text
    hub = hub_module._HUBS["local"]
    for _ in range(100):
        if any(e.role == "hub" for e in hub.state.entries):
            break
        await asyncio.sleep(0.02)
    assert [(f.file_id, f.session_id) for f in hub.held_files()] == [(file_id, hub_id)]
    assert hub.state.entries[0].text == "esses são os anexos\n[attached: ReportOn26-4238v1.txt]"


async def test_a_reply_and_its_chart_reach_the_hub_as_the_project_shows_them(
    client, _hub_home, db_uri, tmp_path, monkeypatch
) -> None:
    from omnigent.entities import NewConversationItem
    from omnigent.entities.conversation import parse_item_data
    from omnigent.server.hub import HubSession
    from tests.server.helpers import create_test_session

    hub_id = (await client.get("/v1/hub")).json()["session_id"]
    target = await create_test_session(client, title="RAL paper")
    chart = b"\x89PNG\r\n\x1a\nchart"
    uploaded = await client.post(
        f"/v1/sessions/{target['id']}/resources/files",
        files={"file": ("speed.png", chart, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    chart_id = uploaded.json()["id"]
    answer = "Done.\n\n| Run | Time |\n|---|---|\n| A | 3 s |"

    def message(content: list[dict], agent: str = "claude-native-ui") -> NewConversationItem:
        data = {"type": "message", "role": "assistant", "agent": agent, "content": content}
        return NewConversationItem(
            type="message", response_id="resp_1", data=parse_item_data("message", data)
        )

    # Stored as a native turn stores them: the answer, then its chart, then the
    # spoken summary, which is the newest assistant message but not the answer.
    store = SqlAlchemyConversationStore(db_uri)
    store.append(target["id"], [message([{"type": "output_text", "text": answer}])])
    store.append(
        target["id"],
        [
            message(
                [
                    {"type": "output_text", "text": "Speed per run"},
                    {
                        "type": "output_file",
                        "file_id": chart_id,
                        "filename": "speed.png",
                        "mime_type": "image/png",
                    },
                ],
                agent="attachment",
            )
        ],
    )
    store.append(
        target["id"],
        [message([{"type": "spoken_summary", "text": "Terminei.", "lang": "pt-BR"}])],
    )

    monkeypatch.setattr(
        hub_module,
        "_file_stores",
        (SqlAlchemyFileStore(db_uri), LocalArtifactStore(str(tmp_path / "artifacts"))),
    )

    async def perform(self, task, *, timeout_s=None):
        return "Terminou, com a tabela e o gráfico."

    monkeypatch.setattr(hub_module.HubRouter, "perform", perform)
    hub = hub_module.hub_for(None)
    state = await hub.require_backend().turn(target["id"], 0.0)
    assert state.reply == answer
    assert [f.filename for f in state.files] == ["speed.png"]

    await hub._report(HubSession(id=target["id"], title="RAL paper"), state)

    said = (await _items(client, hub_id))[-1]
    text, attached = said["content"][0]["text"], said["content"][1]
    assert text.startswith("RAL paper finished: Terminou, com a tabela e o gráfico.")
    assert "| Run | Time |\n|---|---|\n| A | 3 s |" in text
    assert "Done." not in text  # the reply itself stays in its chat
    assert attached["type"] == "output_file" and attached["filename"] == "speed.png"
    assert attached["file_id"] != chart_id  # the hub chat owns its own copy
    copy = await client.get(f"/v1/sessions/{hub_id}/resources/files/{attached['file_id']}/content")
    assert copy.content == chart
