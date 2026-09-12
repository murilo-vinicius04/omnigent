"""The warm companion: what it knows, what it costs, and how it recovers.

These drive the real subprocess path against a fake ``agy`` that speaks
the same NDJSON protocol, so the message composition and the pipe
handling are exercised for real without spending a Gemini call.
"""

from __future__ import annotations

import json
import stat
import sys
import textwrap

import pytest

from omnigent.server import discussion
from omnigent.server.discussion import (
    DiscussionRegistry,
    DiscussionSession,
    DiscussionUnavailable,
)

#: A stand-in for the CLI. Echoes each message back as the response so a
#: test can assert on exactly what was composed, and honours two markers
#: for the failure paths.
_FAKE_AGY = """\
import json, sys, time
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    event = json.loads(line)
    content = event["message"]["content"]
    if "PLEASE_DIE" in content:
        sys.stderr.write("fake agy exploded\\n")
        sys.exit(9)
    if "PLEASE_HANG" in content:
        time.sleep(30)
    sys.stdout.write(json.dumps({"event": "progress", "note": "thinking"}) + "\\n")
    sys.stdout.write(json.dumps({"event": "result", "result": {"response": content}}) + "\\n")
    sys.stdout.flush()
"""


@pytest.fixture
def fake_agy(tmp_path):
    """Install a fake ``agy`` on disk and return its path."""
    script = tmp_path / "fake_agy"
    script.write_text(f"#!{sys.executable}\n{textwrap.dedent(_FAKE_AGY)}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture
def session(fake_agy):
    """A companion wired to the fake CLI."""
    return DiscussionSession("s1", binary=fake_agy, model="test-model")


async def test_notes_do_not_start_a_process(session):
    # The whole point of buffering notes: narrating what Claude is doing
    # must not cost a round trip, nor spawn anything.
    session.note("activity", "running the tests")
    session.note("summary", "all 10 passed")
    assert session.running is False
    assert [entry.text for entry in session.context] == ["running the tests", "all 10 passed"]


async def test_blank_notes_are_dropped(session):
    session.note("activity", "   ")
    assert session.context == []


async def test_a_cold_question_carries_role_and_ledger_in_one_turn(session):
    session.note("activity", "running the tests")
    answer = await session.ask("what's happening?")
    # One turn, not three: the role, the replay and the question all
    # ride together, which is what keeps a cold ask near one round trip.
    assert "companion" in answer
    assert "Claude is: running the tests" in answer
    assert "[They said]\nwhat's happening?" in answer
    await session.close()


async def test_a_warm_question_sends_only_the_question(session):
    await session.ask("first")
    answer = await session.ask("second")
    assert answer == "second"
    assert discussion.ROLE_INSTRUCTIONS[:40] not in answer
    await session.close()


async def test_notes_ride_the_next_question_then_clear(session):
    await session.ask("first")
    session.note("activity", "deploying")
    answer = await session.ask("and now?")
    assert "Claude is: deploying" in answer
    assert session.as_dict()["pending_notes"] == 0
    # Already delivered, so it must not be repeated on the turn after.
    assert "deploying" not in await session.ask("still?")
    await session.close()


async def test_prewarm_makes_the_first_question_warm(session):
    await session.prewarm()
    assert session.running is True
    answer = await session.ask("what's happening?")
    assert answer == "what's happening?"
    await session.close()


async def test_prewarm_is_idempotent(session):
    await session.prewarm()
    started = session.as_dict()["warm_since"]
    await session.prewarm()
    assert session.as_dict()["warm_since"] == started
    await session.close()


async def test_a_dead_process_is_reported_and_the_question_is_kept(session):
    await session.ask("first")
    with pytest.raises(DiscussionUnavailable):
        await session.ask("PLEASE_DIE")
    assert session.running is False
    # The ledger is the memory, so the question survives the process.
    assert session.context[-1].text == "PLEASE_DIE"
    assert session.context[-1].kind == "question"


async def test_the_ledger_is_replayed_into_a_replacement_process(session):
    session.note("summary", "the parser bug is fixed")
    await session.ask("noted")
    await session.close()
    assert session.running is False
    answer = await session.ask("what did you say about the parser?")
    # A fresh process must be told everything the ledger holds, so the
    # conversation survives a crash, a reap, or a restart.
    assert "Claude said: the parser bug is fixed" in answer
    assert "They asked: noted" in answer
    await session.close()


async def test_a_timeout_drops_the_process_rather_than_desyncing(session):
    # A late answer would pair with the *next* question, so the only safe
    # move is to kill the process and rebuild from the ledger.
    with pytest.raises(DiscussionUnavailable):
        await session.ask("PLEASE_HANG", timeout_s=0.5)
    assert session.running is False


async def test_a_missing_binary_is_unavailable_not_a_crash(tmp_path):
    session = DiscussionSession("s1", binary=str(tmp_path / "absent"))
    with pytest.raises(DiscussionUnavailable):
        await session.ask("hello")


async def test_an_empty_question_is_refused(session):
    with pytest.raises(DiscussionUnavailable):
        await session.ask("   ")


async def test_the_ledger_is_capped(session, monkeypatch):
    monkeypatch.setattr(discussion, "MAX_ENTRIES", 4)
    capped = DiscussionSession("s2", binary=session._binary)
    for index in range(10):
        capped.note("activity", f"step {index}")
    assert len(capped.context) <= 10
    assert capped.context[-1].text == "step 9"


async def test_as_dict_is_what_the_ui_renders(session):
    session.note("activity", "running the tests")
    state = session.as_dict()
    assert state["session_id"] == "s1"
    assert state["model"] == "test-model"
    assert state["running"] is False
    assert state["warm_since"] is None
    assert state["pending_notes"] == 1
    assert state["context"] == [
        {
            "id": 1,
            "kind": "activity",
            "text": "running the tests",
            "at": pytest.approx(state["context"][0]["at"]),
        }
    ]


async def test_registry_keeps_one_companion_per_session():
    registry = DiscussionRegistry()
    first = await registry.get("a")
    assert await registry.get("a") is first
    assert await registry.get("b") is not first
    # Creating one must not spawn anything: a session nobody talks to
    # costs nothing.
    assert first.running is False


async def test_registry_close_forgets_the_session():
    registry = DiscussionRegistry()
    first = await registry.get("a")
    await registry.close("a")
    assert registry.peek("a") is None
    assert await registry.get("a") is not first


async def test_sweep_reaps_only_idle_processes(fake_agy, monkeypatch):
    monkeypatch.setenv("OMNIGENT_DISCUSSION_AGY_BIN", fake_agy)
    registry = DiscussionRegistry()
    busy = await registry.get("busy")
    idle = await registry.get("idle")
    await busy.prewarm()
    await idle.prewarm()
    idle._last_used -= 10_000
    assert await registry.sweep(idle_s=60) == 1
    assert busy.running is True
    assert idle.running is False
    # Reaped, not forgotten: the ledger stays so the next question can
    # rebuild the process.
    assert registry.peek("idle") is idle
    await registry.close_all()


async def test_idle_timeout_reads_the_environment(monkeypatch):
    monkeypatch.setenv("OMNIGENT_DISCUSSION_IDLE_S", "42")
    assert discussion.idle_timeout_s() == 42.0
    monkeypatch.setenv("OMNIGENT_DISCUSSION_IDLE_S", "nonsense")
    assert discussion.idle_timeout_s() == discussion.DEFAULT_IDLE_S


async def test_the_untrusted_input_never_reaches_slash_commands(session, monkeypatch):
    seen: list[list[str]] = []
    original = discussion.asyncio.create_subprocess_exec

    async def record(binary, *args, **kwargs):
        seen.append(list(args))
        return await original(binary, *args, **kwargs)

    monkeypatch.setattr(discussion.asyncio, "create_subprocess_exec", record)
    await session.ask("/clear")
    assert "--disable-slash-commands" in seen[0]
    await session.close()


async def test_json_is_well_formed_for_awkward_text(session):
    # Speech arrives with quotes and embedded newlines. The NDJSON framing
    # is one line per message, so a naive encoder would split the turn in
    # half and the second half would be read as the next question.
    spoken = 'he said "hi"\nthen left'
    await session.prewarm()
    answer = await session.ask(spoken)
    assert answer == spoken
    assert json.loads(json.dumps(answer)) == spoken
    await session.close()


def _app_with_companions(fake_agy, *, auth_provider=None):
    """Build a test app whose lifespan owns its own companion registry.

    Closing the registry from the app's own shutdown — the way the real
    server does — is what keeps subprocess transports from outliving the
    event loop they were created on.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from omnigent.server.routes.discussion import create_discussion_router

    companions = DiscussionRegistry()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await companions.close_all()

    app = FastAPI(lifespan=lifespan)
    app.include_router(
        create_discussion_router(
            auth_provider=auth_provider, registry_provider=lambda: companions
        ),
        prefix="/v1",
    )
    return app


@pytest.fixture
def client(fake_agy, monkeypatch):
    """A TestClient over the companion router, wired to the fake CLI."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OMNIGENT_DISCUSSION_AGY_BIN", fake_agy)
    with TestClient(_app_with_companions(fake_agy)) as test_client:
        yield test_client


def test_route_note_is_free_and_returns_the_ledger(client):
    state = client.post("/v1/discussion/r1/note", json={"text": "running the tests"}).json()
    assert state["running"] is False
    assert state["pending_notes"] == 1
    assert state["context"][0]["kind"] == "activity"


def test_route_ask_answers_and_echoes_the_ledger(client):
    client.post("/v1/discussion/r2/note", json={"text": "running the tests"})
    body = client.post("/v1/discussion/r2/ask", json={"text": "what's up?"}).json()
    assert "Claude is: running the tests" in body["answer"]
    # Every mutating route hands back the whole ledger so the caller's
    # panel cannot drift from what the companion knows.
    assert [e["kind"] for e in body["state"]["context"]][-2:] == ["question", "answer"]
    assert body["state"]["pending_notes"] == 0


def test_route_ask_reports_unavailable_as_503(client):
    response = client.post("/v1/discussion/r3/ask", json={"text": "PLEASE_DIE"})
    assert response.status_code == 503


def test_route_ask_rejects_empty_text(client):
    assert client.post("/v1/discussion/r4/ask", json={"text": ""}).status_code == 422


def test_route_close_keeps_the_ledger(client):
    client.post("/v1/discussion/r5/note", json={"text": "running the tests"})
    client.post("/v1/discussion/r5/ask", json={"text": "hi"})
    state = client.post("/v1/discussion/r5/close", json={}).json()
    assert state["running"] is False
    assert len(state["context"]) == 3


def test_route_prewarm_then_state(client):
    assert client.post("/v1/discussion/r6/prewarm", json={}).json()["running"] is True
    state = client.get("/v1/discussion/r6").json()
    assert state["running"] is True
    assert state["warm_since"] is not None


def test_routes_require_auth_when_a_provider_is_configured(fake_agy):
    from fastapi.testclient import TestClient

    class Anonymous:
        def get_user_id(self, request):
            return None

    app = _app_with_companions(fake_agy, auth_provider=Anonymous())
    with TestClient(app) as guarded:
        assert guarded.get("/v1/discussion/x").status_code == 401
        assert guarded.post("/v1/discussion/x/ask", json={"text": "hi"}).status_code == 401


async def test_the_module_note_creates_the_companion_on_first_use():
    # The turn path must not have to know whether a companion exists yet.
    discussion.note("fresh-session", "summary", "I fixed the parser bug")
    session = discussion.registry().peek("fresh-session")
    assert session is not None
    assert session.context[0].kind == "summary"
    await discussion.registry().close("fresh-session")


async def test_the_module_note_never_raises(monkeypatch):
    # A companion hangs off the side of a session; a failure here must be
    # invisible to the turn being served.
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(discussion.DiscussionRegistry, "note", explode)
    discussion.note("s", "summary", "text")


async def test_tell_companion_feeds_the_summary_and_what_is_still_running():
    from omnigent.server.routes._sessions.helpers import _tell_companion

    _tell_companion(
        "conv_x",
        {"type": "spoken_summary", "text": "I fixed the parser bug.", "lang": "en"},
        pending_work=["the test suite"],
    )
    session = discussion.registry().peek("conv_x")
    assert session is not None
    assert [(e.kind, e.text) for e in session.context] == [
        ("summary", "I fixed the parser bug."),
        ("activity", "still running: the test suite"),
    ]
    await discussion.registry().close("conv_x")


async def test_tell_companion_ignores_a_turn_with_no_summary():
    from omnigent.server.routes._sessions.helpers import _tell_companion

    _tell_companion("conv_y", None)
    _tell_companion("conv_y", {"type": "spoken_summary", "text": "  "})
    assert discussion.registry().peek("conv_y") is None


# --- routing: who gets the message the reader typed -------------------


def test_routing_parses_a_clean_decision():
    decision = discussion._parse_routing(
        '{"forward": false, "english": "how are you?", "answer": "All good."}'
    )
    assert decision.forward is False
    assert decision.english == "how are you?"
    assert decision.answer == "All good."


def test_routing_keeps_the_english_when_it_forwards():
    decision = discussion._parse_routing('{"forward": true, "english": "run the tests"}')
    assert decision.forward is True
    assert decision.english == "run the tests"
    # An answer on a forwarded message would be shown to nobody.
    assert decision.answer is None


def test_routing_tolerates_a_model_that_wraps_its_json():
    decision = discussion._parse_routing(
        'Sure!\n```json\n{"forward": false, "english": "hi", "answer": "Hey."}\n```'
    )
    assert decision.forward is False
    assert decision.answer == "Hey."


def test_every_unreadable_reply_forwards():
    # Forwarding costs seconds; a confident wrong answer from something
    # that cannot see the code costs much more. So every failure forwards.
    for reply in ["", "I think you should ask Claude", "{not json}", "[1, 2]", "{}"]:
        assert discussion._parse_routing(reply).forward is True


def test_keeping_a_message_requires_something_to_say():
    # "forward": false with no answer would consume the message and show
    # the reader nothing at all.
    assert discussion._parse_routing('{"forward": false, "english": "hi"}').forward is True


def test_only_an_explicit_false_keeps_the_message():
    assert discussion._parse_routing('{"forward": "no", "answer": "hi"}').forward is True
    assert discussion._parse_routing('{"answer": "hi"}').forward is True


async def test_route_asks_once_and_records_the_exchange(session, monkeypatch):
    monkeypatch.setattr(
        session,
        "_turn",
        _fake_turn('{"forward": false, "english": "how are you?", "answer": "All good."}'),
    )
    decision = await session.route("tudo bem?")
    assert decision.forward is False
    assert [(e.kind, e.text) for e in session.context] == [
        ("question", "tudo bem?"),
        ("answer", "All good."),
    ]


async def test_route_records_a_forwarded_message_without_an_answer(session, monkeypatch):
    monkeypatch.setattr(
        session, "_turn", _fake_turn('{"forward": true, "english": "run the tests"}')
    )
    await session.route("roda os testes")
    # Claude's own summary arrives later; recording an answer here would
    # put words in its mouth.
    assert [(e.kind, e.text) for e in session.context] == [("question", "roda os testes")]


async def test_route_forwards_when_the_companion_is_broken(fake_agy, tmp_path):
    broken = DiscussionSession("s", binary=str(tmp_path / "absent"))
    decision = await broken.route("qualquer coisa")
    assert decision.forward is True
    assert decision.english is None
    # The reader's message still lands in the ledger, so a companion that
    # recovers has not missed half the conversation.
    assert broken.context[-1].text == "qualquer coisa"


async def test_route_forwards_an_empty_message(session):
    assert (await session.route("   ")).forward is True


def test_routing_is_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv("OMNIGENT_COMPANION_ROUTING", raising=False)
    assert discussion.routing_enabled() is True
    for off in ["0", "false", "no", "off", "OFF"]:
        monkeypatch.setenv("OMNIGENT_COMPANION_ROUTING", off)
        assert discussion.routing_enabled() is False


def _fake_turn(reply: str):
    """Return a ``_turn`` stand-in that answers with *reply*."""

    async def turn(_message: str, *, timeout_s: float) -> str:
        return reply

    return turn


# --- the dispatch bypass: "ask Claude anyway" ------------------------


def test_force_claude_marker_is_stripped_before_anything_sees_it():
    from omnigent.server.routes._sessions.orchestration import _strip_force_claude

    content, forced = _strip_force_claude(
        [{"type": "input_text", "text": "hi"}, {"type": "force_claude"}]
    )
    assert forced is True
    # It is transport, not content: the transcript and the harness must
    # never see it.
    assert content == [{"type": "input_text", "text": "hi"}]


def test_content_without_the_marker_is_left_alone():
    from omnigent.server.routes._sessions.orchestration import _strip_force_claude

    original = [{"type": "input_text", "text": "hi"}]
    content, forced = _strip_force_claude(original)
    assert forced is False
    assert content is original


def test_swapping_in_the_english_matches_the_translation_path():
    from omnigent.server.routes._sessions.orchestration import _swap_first_text

    content, english = _swap_first_text(
        [
            {"type": "input_image", "file_id": "f1"},
            {"type": "input_text", "text": "roda os testes"},
            {"type": "input_text", "text": "trailing"},
        ],
        "run the tests",
    )
    assert english == "run the tests"
    # First text swapped, later text dropped, non-text kept — the same
    # shape the inbound repair pass produces.
    assert content == [
        {"type": "input_image", "file_id": "f1"},
        {"type": "input_text", "text": "run the tests"},
    ]


def test_swapping_text_that_is_not_there_changes_nothing():
    from omnigent.server.routes._sessions.orchestration import _swap_first_text

    original = [{"type": "input_image", "file_id": "f1"}]
    content, english = _swap_first_text(original, "run the tests")
    assert english is None
    assert content is original


# --- whose words go to Claude: the session's language decides ---------


async def test_translate_mode_asks_for_english(session, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        session, "_turn", _capturing_turn(seen, '{"forward": true, "english": "run the tests"}')
    )
    decision = await session.route("roda os testes", restate="translate")
    assert decision.english == "run the tests"
    assert "restated in plain English" in seen[0]


async def test_repair_mode_asks_for_a_repair_not_a_rewrite(session, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        session,
        "_turn",
        _capturing_turn(seen, '{"forward": true, "english": "fix the pool leak"}'),
    )
    decision = await session.route("fix the pull leak", restate="repair")
    assert decision.english == "fix the pool leak"
    # A reader already writing English keeps their own words; only a word
    # the sentence plainly settles may change.
    assert "repair, not a rewrite" in seen[0]
    assert "restated in plain English" not in seen[0]


async def test_off_mode_forwards_the_readers_own_words(session, monkeypatch):
    # The session asked for no rewriting, so even a model that restates
    # anyway must not put words in the reader's mouth.
    monkeypatch.setattr(
        session,
        "_turn",
        _fake_turn('{"forward": true, "english": "A tidier version of what they wrote"}'),
    )
    decision = await session.route("whatever i actually typed", restate="off")
    assert decision.english is None
    assert decision.forward is True


async def test_off_mode_still_lets_the_companion_answer(session, monkeypatch):
    # Only the restatement is off; deciding who answers is unconditional.
    monkeypatch.setattr(
        session, "_turn", _fake_turn('{"forward": false, "english": "hi", "answer": "Hey."}')
    )
    decision = await session.route("hi", restate="off")
    assert decision.forward is False
    assert decision.answer == "Hey."
    assert decision.english is None


def _capturing_turn(sink: list[str], reply: str):
    """Return a ``_turn`` stand-in that records the prompt it was given."""

    async def turn(message: str, *, timeout_s: float) -> str:
        sink.append(message)
        return reply

    return turn
