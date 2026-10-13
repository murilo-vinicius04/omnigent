"""The hub chat: projects, routing decisions, the log, sending, watching, its routes."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import textwrap
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server import hub as hub_module
from omnigent.server.discussion import DiscussionRegistry, DiscussionUnavailable
from omnigent.server.hub import (
    EMPTY_REPLY_NUDGE,
    Hub,
    HubBackend,
    HubCandidate,
    HubFile,
    HubProject,
    HubRouter,
    HubSession,
    HubState,
    ProjectRecord,
    TurnState,
    assign_handles,
    find_project,
    find_session,
    parse_decision,
    project_line,
    session_line,
)
from omnigent.server.routes.hub import create_hub_router

AMARRAS = "d14bc7496a6a42b6b6a0730b3f4f5f20"
BRASA = "ba9b8af460dd40f5b333b58f72d0683c"
AUDIO = "247c77f5f57748b0a2747e4b14548cac"
#: Project ids: their handles are "aaaa" and "bbbb".
P_AMARRAS = "aaaa1111aaaa1111aaaa1111aaaa1111"
P_BRASA = "bbbb2222bbbb2222bbbb2222bbbb2222"


@pytest.mark.parametrize(
    ("reply", "action", "handle", "text", "message"),
    [
        (
            '{"send": "d14b", "message": "Run the tests.", "say": "Sent to amarras."}',
            "send",
            "d14b",
            "Sent to amarras.",
            "Run the tests.",
        ),
        ('{"check": "ba9b", "question": "Disk free?"}', "check", "ba9b", "", "Disk free?"),
        ('{"status": "247c"}', "status", "247c", "", ""),
        ('{"ask": "Which one?"}', "ask", None, "Which one?", ""),
        ('{"new_project": "robot arm", "say": "Ok."}', "new_project", None, "Ok.", "robot arm"),
        ('Sure! {"answer": "Oi!"} ', "answer", None, "Oi!", ""),
        ("no json here", "none", None, "no json here", ""),
        ('{"answer": ', "none", None, '{"answer":', ""),
    ],
)
def test_router_replies_parse_into_one_action(reply, action, handle, text, message) -> None:
    decision = parse_decision(reply)
    assert (decision.action, decision.handle, decision.text, decision.message) == (
        action,
        handle,
        text,
        message,
    )


def test_handles_are_the_shortest_unique_prefix() -> None:
    handles = assign_handles(["abcd1111", "abcd2222", "ffff0000"])
    assert handles == {"abcd1111": "abcd1", "abcd2222": "abcd2", "ffff0000": "ffff"}


SESSIONS = [
    HubSession(
        id=AMARRAS, title="Project Analysis and Organization", nickname="amarras", handle="d14b"
    ),
    HubSession(id=BRASA, title="Análise de projeto clonado", nickname="brasa", handle="ba9b"),
    HubSession(id=AUDIO, title="Corrigir áudio omnigent", handle="247c", status="running"),
]


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("d14b", AMARRAS),
        ("amarras", AMARRAS),
        ("a marras", AMARRAS),
        ("brassa", BRASA),
        ("corrigir audio omnigent", AUDIO),
        ("kitchen", None),
        ("", None),
    ],
)
def test_sessions_are_found_by_handle_nickname_or_title(spoken, expected) -> None:
    found = find_session(SESSIONS, spoken)
    assert (found.id if found else None) == expected


def test_projects_are_found_by_handle_name_or_their_chats_title() -> None:
    projects = [
        HubProject(id=P_AMARRAS, name="amarras", handle="aaaa", chat=SESSIONS[0]),
        HubProject(id=P_BRASA, name="brasa", handle="bbbb"),
    ]
    assert find_project(projects, "aaaa").id == P_AMARRAS
    assert find_project(projects, "a marras").id == P_AMARRAS
    assert find_project(projects, "project analysis and organization").id == P_AMARRAS
    assert find_project(projects, "brassa").id == P_BRASA
    assert find_project(projects, "kitchen") is None


def test_a_project_without_a_chat_says_nothing_can_be_sent_to_it() -> None:
    line = project_line(HubProject(id=P_BRASA, name="brasa", handle="bbbb"))
    assert line == "[bbbb] brasa · no chat yet: nothing can be sent to it"
    many = project_line(
        HubProject(
            id=P_AMARRAS,
            name="amarras",
            handle="aaaa",
            chats=3,
            chat=replace(SESSIONS[0], handle="aaaa"),
        )
    )
    assert many.startswith('[aaaa] amarras · "Project Analysis and Organization"')
    assert many.endswith("3 chats, this is the newest")


def test_a_session_line_says_what_the_router_needs() -> None:
    line = session_line(
        HubSession(
            id=AUDIO,
            title="Corrigir áudio",
            nickname="audio",
            handle="247c",
            status="running",
            now="Bash: pytest -q",
            first_ask="fix the audio",
            last_ask="is the watchdog live?",
            summary="Moved to Gemini 3.8 Live.",
            pending_approvals=1,
        )
    )
    assert line.startswith('[247c] audio · "Corrigir áudio" · WORKING (now: Bash: pytest -q)')
    assert "waiting for 1 approval(s)" in line
    assert 'started with: "fix the audio"' in line
    assert 'last asked: "is the watchdog live?"' in line
    assert 'latest update: "Moved to Gemini 3.8 Live."' in line


# -- the log -----------------------------------------------------------------


def test_the_log_and_names_survive_a_restart(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    state.append(role="you", kind="message", text="oi", via="typed")
    state.append(role="hub", kind="sent", text="Sent.", session_id=AMARRAS, detail="Run it.")
    state.name(AMARRAS, "amarras")
    again = HubState("u1", root=tmp_path)
    assert [(e.id, e.role, e.kind, e.text) for e in again.entries] == [
        (1, "you", "message", "oi"),
        (2, "hub", "sent", "Sent."),
    ]
    assert again.entries[1].detail == "Run it."
    assert again.names == {AMARRAS: "amarras"}


def test_the_log_keeps_only_the_newest_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "MAX_LOG", 3)
    state = HubState("u1", root=tmp_path)
    for n in range(5):
        state.append(role="you", kind="message", text=f"m{n}")
    assert [e.text for e in HubState("u1", root=tmp_path).entries] == ["m2", "m3", "m4"]


async def test_waiting_for_the_next_entry_wakes_on_append(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    waiter = asyncio.ensure_future(state.wait_after(0, 5.0))
    await asyncio.sleep(0.01)
    state.append(role="hub", kind="message", text="hello")
    got = await asyncio.wait_for(waiter, 1.0)
    assert [e.text for e in got] == ["hello"]
    assert await state.wait_after(1, 0.01) == []


# -- the hub, with a scripted router and a fake server --------------------------


class _Router:
    """Stands in for the Gemini router: replies are scripted per turn."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.remembered: list[str] = []
        self.performed: list[str] = []
        self.down = False
        self.why_down = "no answer within 90s"
        self.report = "It fixed the scanner and all tests pass."

    async def decide(self, prompt: str, *, said: str, timeout_s: float) -> str:
        if self.down:
            raise DiscussionUnavailable(self.why_down)
        self.prompts.append(prompt)
        return self.replies.pop(0)

    async def follow_up(self, prompt: str, *, timeout_s: float) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)

    async def perform(self, task: str, *, timeout_s: float | None = None) -> str:
        self.performed.append(task)
        return self.report

    def remember(self, text: str) -> None:
        self.remembered.append(text)


class _Server:
    """The projects, chats, sends and turn states the hub sees."""

    def __init__(self) -> None:
        self.sessions = [
            HubSession(
                id=AMARRAS,
                title="Project Analysis",
                first_ask="petrobras chains docs",
                project_id=P_AMARRAS,
            ),
            HubSession(id=BRASA, title="Cloned project review", project_id=P_BRASA),
            HubSession(id="loose0", title="Greeting"),
        ]
        self.projects = [
            ProjectRecord(id=P_AMARRAS, name="amarras", chat_ids=(AMARRAS,), created_at=1.0),
            ProjectRecord(id=P_BRASA, name="brasa", chat_ids=(BRASA,), created_at=2.0),
        ]
        self.sent: list[tuple[str, str]] = []
        self.checked: list[tuple[str, str]] = []
        self.turns: list[TurnState] = []
        self.fail_send = False
        self.created: list[tuple[str, str | None]] = []
        self.files: list[list[str]] = []

    def _file(self, session_id: str, project_id: str) -> None:
        self.projects = [
            replace(r, chat_ids=tuple(c for c in r.chat_ids if c != session_id))
            for r in self.projects
        ]
        self.projects = [
            replace(r, chat_ids=(session_id, *r.chat_ids)) if r.id == project_id else r
            for r in self.projects
        ]
        self.sessions = [
            replace(s, project_id=project_id) if s.id == session_id else s for s in self.sessions
        ]

    def backend(self) -> HubBackend:
        async def sessions(ids):
            return [s for s in self.sessions if s.id in ids]

        async def listing():
            return [
                HubCandidate(id=s.id, title=s.title, updated_at=s.updated_at)
                for s in self.sessions
                if not s.project_id
            ]

        async def activity(session_id):
            return f"Claude is idle: the last task finished. ({session_id[:4]})"

        async def send(session_id, text, files=()):
            if self.fail_send:
                raise RuntimeError("runner gone")
            self.sent.append((session_id, text))
            self.files.append([f.filename for f in files])

        async def check(session_id, question):
            self.checked.append((session_id, question))
            return "18 GB free."

        async def turn(session_id, since):
            return self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]

        async def projects():
            return list(self.projects)

        async def create_project(name, from_chat):
            self.created.append((name, from_chat))
            record = ProjectRecord(id=f"new{len(self.created)}" + "0" * 28, name=name)
            self.projects.append(record)
            if from_chat:
                self._file(from_chat, record.id)
            return next(r for r in self.projects if r.id == record.id)

        async def rename_project(project_id, name):
            self.projects = [
                replace(r, name=name) if r.id == project_id else r for r in self.projects
            ]

        async def delete_project(project_id):
            found = next(r for r in self.projects if r.id == project_id)
            for chat_id in found.chat_ids:
                self._file(chat_id, "")
            self.projects = [r for r in self.projects if r.id != project_id]
            return len(found.chat_ids)

        async def file_chat(session_id, project_id):
            self._file(session_id, project_id)

        return HubBackend(
            sessions=sessions,
            listing=listing,
            activity=activity,
            send=send,
            check=check,
            turn=turn,
            projects=projects,
            create_project=create_project,
            rename_project=rename_project,
            delete_project=delete_project,
            file_chat=file_chat,
        )


def _hub(tmp_path, router: _Router, server: _Server) -> Hub:
    async def factory():
        return router

    hub = Hub("u1", router_factory=factory, state=HubState("u1", root=tmp_path), poll_s=0.01)
    hub.bind(server.backend())
    return hub


async def _settle(hub: Hub, *, until=None, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until is None and not hub._tasks:
            return
        if until is not None and until():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("hub did not settle")


async def test_a_message_goes_to_the_project_it_is_about_and_comes_back(tmp_path) -> None:
    router = _Router(
        '{"send": "aaaa", "message": "Summarize the mean error.", "say": "Mandei pra amarras."}'
    )
    server = _Server()
    server.turns = [
        TurnState(status="running"),
        TurnState(status="idle", reply="Mean error is 3 mm."),
        TurnState(status="idle", reply="Mean error is 3 mm across all 40 frets."),
    ]
    hub = _hub(tmp_path, router, server)

    said = hub.submit("manda a amarras resumir o erro médio")
    assert (said.role, said.kind, said.via) == ("you", "message", "typed")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))

    # Sent to the project's chat; the router saw projects, not loose chats.
    assert server.sent == [(AMARRAS, "Summarize the mean error.")]
    assert '[aaaa] amarras · "Project Analysis"' in router.prompts[0]
    assert "[bbbb] brasa" in router.prompts[0] and "Greeting" not in router.prompts[0]
    assert "manda a amarras" in router.prompts[0]
    kinds = [(e.role, e.kind) for e in hub.state.entries]
    assert kinds == [("you", "message"), ("hub", "sent"), ("hub", "finished")]
    sent, finished = hub.state.entries[1], hub.state.entries[2]
    assert sent.text == "Mandei pra amarras." and sent.detail == "Summarize the mean error."
    assert (sent.session_id, sent.session_name) == (AMARRAS, "amarras")
    assert finished.text == "amarras finished: It fixed the scanner and all tests pass."
    # The newest reply, read after the chat stayed idle, is the one reported.
    assert finished.detail == "Mean error is 3 mm across all 40 frets."
    assert "Mean error is 3 mm across all 40 frets." in router.performed[0]


class _Chat:
    """Stands in for the hub's conversation: records what it is shown."""

    def __init__(self, language: str = "") -> None:
        self.session_id = "hub0"
        self.posted: list[str] = []
        self._language = language

    def post(self, entry) -> None:
        self.posted.append(entry.text)

    def post_user(self, text: str) -> None:
        self.posted.append(text)

    def busy(self, working: bool) -> None:
        pass

    async def language(self) -> str:
        return self._language


@pytest.mark.parametrize(
    ("tag", "phrase"),
    [
        ("pt-BR", "Brazilian Portuguese (pt-BR)"),
        ("es", "Spanish (es)"),
        ("ja", "ja"),
        ("auto", "the language they wrote in"),
        ("", "the language they wrote in"),
    ],
)
def test_the_router_is_told_the_hubs_language(tag, phrase) -> None:
    assert hub_module.language_phrase(tag) == phrase


@pytest.mark.parametrize(
    ("language", "told"),
    [
        ("pt-BR", "request in Brazilian Portuguese (pt-BR) for that Claude"),
        ("", "request in the language they wrote in for that Claude"),
    ],
)
async def test_a_send_goes_in_the_language_the_hub_is_set_to(tmp_path, language, told) -> None:
    router = _Router('{"send": "aaaa", "message": "Resuma o erro médio.", "say": "Mandei."}')
    server = _Server()
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)
    hub.chat = _Chat(language)
    hub.submit("manda a amarras resumir o erro médio")
    await _settle(hub, until=lambda: bool(server.sent))
    assert told in router.prompts[0]
    assert "English" not in router.prompts[0]
    assert server.sent == [(AMARRAS, "Resuma o erro médio.")]
    await hub.close()


TABLE = {"kind": "table", "label": "t", "content": "| a | b |\n|---|---|\n| 1 | 2 |"}
CHART = {"kind": "file", "label": "speed", "content": "f_chart", "filename": "speed.png"}


def _copying_hub(tmp_path, server: _Server, copied: list) -> Hub:
    async def import_files(source, target, files):
        copied.append((source, target, [f.file_id for f in files]))
        return [{"type": "output_file", "file_id": f"copy_{f.file_id}"} for f in files]

    router = _Router('{"send": "aaaa", "message": "Run it.", "say": "Mandei."}')
    hub = _hub(tmp_path, router, server)
    hub.bind(replace(server.backend(), import_files=import_files))
    hub.chat = _Chat()
    return hub


async def test_a_report_waits_for_the_projects_summary_and_shows_what_it_picked(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(hub_module, "SUMMARY_WAIT_S", 5.0)
    server = _Server()
    waiting = TurnState(status="idle", reply="Ran.", summary_due=True)
    summarized = TurnState(status="idle", reply="Ran.", show=(TABLE, CHART))
    server.turns = [TurnState(status="running"), waiting, waiting, waiting, summarized]
    copied: list = []
    hub = _copying_hub(tmp_path, server, copied)
    hub.submit("roda")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))
    finished = next(e for e in hub.state.entries if e.kind == "finished")
    assert finished.show == [TABLE]  # the chart is copied, not linked
    assert copied == [(AMARRAS, "hub0", ["f_chart"])]
    assert finished.files == [{"type": "output_file", "file_id": "copy_f_chart"}]
    await hub.close()


async def test_a_summary_that_never_comes_does_not_hold_the_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "SUMMARY_WAIT_S", 0.05)
    server = _Server()
    server.turns = [
        TurnState(status="running"),
        TurnState(status="idle", reply="Ran.", summary_due=True),
    ]
    hub = _copying_hub(tmp_path, server, [])
    hub.submit("roda")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))
    finished = next(e for e in hub.state.entries if e.kind == "finished")
    assert finished.show is None and finished.detail == "Ran."
    await hub.close()


async def test_a_reply_waiting_on_its_summary_is_never_called_missing(
    tmp_path, monkeypatch
) -> None:
    # The summary wait (10 polls here, 8 live) outlasts the 6 idle polls after
    # which a turn with no reply is called empty; a turn that has one is not.
    monkeypatch.setattr(hub_module, "SUMMARY_WAIT_S", 0.1)
    server = _Server()
    server.turns = [
        TurnState(status="running"),
        TurnState(status="idle", reply="Ran.", summary_due=True),
    ]
    hub = _copying_hub(tmp_path, server, [])
    hub.submit("roda")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))
    [finished] = [e for e in hub.state.entries if e.kind == "finished"]
    assert finished.detail == "Ran."
    assert "without writing a reply" not in finished.text
    await hub.close()


async def test_a_report_never_repeats_the_finished_label(tmp_path) -> None:
    router = _Router('{"send": "aaaa", "message": "Run it.", "say": "Mandei."}')
    router.report = "AMARRAS finished: It ran."
    server = _Server()
    server.turns = [TurnState(status="running"), TurnState(status="idle", reply="Ran.")] * 2
    hub = _hub(tmp_path, router, server)
    hub.submit("roda")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))
    finished = next(e for e in hub.state.entries if e.kind == "finished")
    assert finished.text == "amarras finished: It ran."
    assert hub_module._without_label("It finished: fine", "amarras finished:") == (
        "It finished: fine"
    )
    await hub.close()


async def test_a_message_goes_to_the_projects_newest_chat(tmp_path) -> None:
    router = _Router('{"send": "bbbb", "message": "Run it.", "say": "Sent."}')
    server = _Server()
    server.turns = [TurnState(status="idle", reply="Done.")]
    server._file("loose0", P_BRASA)  # now brasa's newest chat
    hub = _hub(tmp_path, router, server)
    hub.submit("run it in brasa")
    await _settle(hub)
    assert server.sent == [("loose0", "Run it.")]
    await hub.close()


async def test_an_unknown_project_sends_nothing(tmp_path) -> None:
    router = _Router('{"send": "zzzz", "message": "x", "say": "Sent."}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("send it to the kitchen")
    await _settle(hub)
    assert server.sent == []
    assert hub.state.entries[-1].text == (
        "I couldn't tell which project you mean. Your projects: brasa, amarras."
    )


async def test_a_project_without_a_chat_gets_a_link_to_start_one(tmp_path) -> None:
    router = _Router('{"send": "cccc", "message": "Build it.", "say": "Sent."}')
    server = _Server()
    server.projects.append(ProjectRecord(id="cccc" + "3" * 28, name="robot arm"))
    hub = _hub(tmp_path, router, server)
    hub.submit("tell robot arm to build it")
    await _settle(hub)
    assert server.sent == []
    last = hub.state.entries[-1]
    assert last.kind == "attention" and "robot arm has no chat yet" in last.text
    assert last.link == "/?project=robot%20arm"


async def test_a_status_question_reads_the_project_live(tmp_path) -> None:
    router = _Router('{"status": "bbbb"}', '{"answer": "Brasa terminou e está parada."}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("o que a brasa tá fazendo?")
    await _settle(hub)
    assert "Claude is idle" in router.prompts[1] and "(ba9b)" in router.prompts[1]
    last = hub.state.entries[-1]
    assert (last.kind, last.text, last.session_id) == (
        "status",
        "Brasa terminou e está parada.",
        BRASA,
    )


async def test_a_check_runs_read_only_and_reports(tmp_path) -> None:
    router = _Router('{"check": "aaaa", "question": "How much disk is free?"}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("quanto de disco livre?")
    await _settle(hub)
    assert server.checked == [(AMARRAS, "How much disk is free?")]
    assert [e.text for e in hub.state.entries if e.kind == "check"] == [
        "Checking on amarras…",
        "18 GB free.",
    ]


async def test_a_router_that_cannot_answer_says_so_and_sends_nothing(tmp_path) -> None:
    router = _Router()
    router.down = True
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("manda pra brasa")
    await _settle(hub)
    assert server.sent == []
    assert hub.state.entries[-1].kind == "error"
    assert "took over 90 s" in hub.state.entries[-1].text
    assert "nothing was sent" in hub.state.entries[-1].text

    router.why_down = "companion process ended: no stderr"
    hub.submit("manda pra brasa")
    await _settle(hub)
    assert "router is not answering" in hub.state.entries[-1].text


async def test_a_failed_send_is_reported_and_not_watched(tmp_path) -> None:
    router = _Router('{"send": "bbbb", "message": "Run it.", "say": "Sent."}')
    server = _Server()
    server.fail_send = True
    hub = _hub(tmp_path, router, server)
    hub.submit("run it in brasa")
    await _settle(hub)
    last = hub.state.entries[-1]
    assert last.kind == "error" and "failed (RuntimeError); nothing was sent" in last.text
    assert hub._watches == {}


async def test_a_project_waiting_for_approval_is_flagged_once(tmp_path) -> None:
    router = _Router('{"send": "bbbb", "message": "Deploy.", "say": "Sent."}')
    server = _Server()
    server.turns = [
        TurnState(status="waiting", pending_approvals=1),
        TurnState(status="waiting", pending_approvals=1),
        TurnState(status="idle", reply="Deployed."),
        TurnState(status="idle", reply="Deployed."),
    ]
    hub = _hub(tmp_path, router, server)
    hub.submit("deploy brasa")
    await _settle(hub, until=lambda: any(e.kind == "finished" for e in hub.state.entries))
    attention = [e.text for e in hub.state.entries if e.kind == "attention"]
    assert attention == ["brasa is waiting for your approval."]


async def test_a_project_that_never_starts_is_flagged(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "NOT_STARTED_S", 0.05)
    router = _Router('{"send": "bbbb", "message": "Go.", "say": "Sent."}')
    server = _Server()
    server.turns = [TurnState(status="idle")]
    hub = _hub(tmp_path, router, server)
    hub.submit("go brasa")
    await _settle(hub, until=lambda: any(e.kind == "attention" for e in hub.state.entries))
    assert "hasn't started on it yet" in hub.state.entries[-1].text


# -- attached files ---------------------------------------------------------------

REVIEWS = [
    HubFile(file_id="f1", filename="Review541750(Reviewer2).txt", session_id="hubconv"),
    HubFile(file_id="f2", filename="ReportOn26-4238v1.txt", session_id="hubconv"),
]


def test_files_are_read_from_a_messages_blocks() -> None:
    blocks = [
        {"type": "input_file", "file_id": "f1", "filename": "a.txt"},
        {"type": "input_image", "file_id": "f2", "filename": "b.png"},
        {"type": "input_text", "text": "these are the attachments"},
        {"type": "input_file", "filename": "no id"},
    ]
    assert hub_module.attached_files(blocks, "hubconv") == [
        HubFile(file_id="f1", filename="a.txt", kind="input_file", session_id="hubconv"),
        HubFile(file_id="f2", filename="b.png", kind="input_image", session_id="hubconv"),
    ]


async def test_attached_files_go_with_the_send_and_only_once(tmp_path) -> None:
    router = _Router(
        '{"send": "bbbb", "message": "Here are the reviews.", "say": "Mandei pra brasa."}',
        '{"send": "bbbb", "message": "And one more thing.", "say": "Mandei."}',
    )
    server = _Server()
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)
    said = hub.submit("esses são os anexos", files=REVIEWS)
    await _settle(hub)
    assert said.text.endswith("[attached: Review541750(Reviewer2).txt, ReportOn26-4238v1.txt]")
    assert "[Files they attached, which go with the next send: Review541750" in router.prompts[0]
    assert server.files == [["Review541750(Reviewer2).txt", "ReportOn26-4238v1.txt"]]
    sent = next(e for e in hub.state.entries if e.kind == "sent")
    assert sent.text == (
        "Mandei pra brasa. With 2 files: Review541750(Reviewer2).txt, ReportOn26-4238v1.txt."
    )
    assert sent.detail.endswith("Attached: Review541750(Reviewer2).txt, ReportOn26-4238v1.txt")
    hub.submit("e mais uma coisa")
    await _settle(hub)
    assert server.files[-1] == []  # carried once, not again
    assert "[Files they attached, which go" not in router.prompts[-1]
    await hub.close()


async def test_files_alone_wait_for_where_they_go(tmp_path) -> None:
    router = _Router('{"send": "aaaa", "message": "Read the reviews.", "say": "Sent."}')
    server = _Server()
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)
    hub.submit("", files=REVIEWS[:1])
    await _settle(hub)
    assert router.prompts == []  # nothing to route yet
    assert hub.state.entries[-1].text.startswith("Got 1 file. Tell me which project")
    hub.submit("manda pra amarras ler")
    await _settle(hub)
    assert server.files == [["Review541750(Reviewer2).txt"]]
    await hub.close()


async def test_files_stay_when_the_send_fails_and_expire_later(tmp_path, monkeypatch) -> None:
    router = _Router('{"send": "bbbb", "message": "Reviews.", "say": "Sent."}')
    server = _Server()
    server.fail_send = True
    hub = _hub(tmp_path, router, server)
    hub.submit("manda pra brasa", files=REVIEWS)
    await _settle(hub)
    assert hub.state.entries[-1].text.endswith("The 2 files stay here for the next try.")
    assert [f.file_id for f in hub.held_files()] == ["f1", "f2"]
    monkeypatch.setattr(hub_module, "FILES_HOLD_S", 0.0)
    assert hub.held_files() == []


# -- a server restart ----------------------------------------------------------


async def test_a_send_still_waiting_is_reported_after_a_restart(tmp_path) -> None:
    router = _Router('{"send": "bbbb", "message": "Run it.", "say": "Sent."}')
    server = _Server()
    server.turns = [TurnState(status="running")]
    hub = _hub(tmp_path, router, server)
    hub.submit("run it in brasa")
    await _settle(hub)
    assert set(HubState("u1", root=tmp_path).watches) == {BRASA}
    await hub.close()  # the server stops mid-watch
    assert set(HubState("u1", root=tmp_path).watches) == {BRASA}  # still owed

    server.turns = [TurnState(status="idle", reply="All tests pass.")]
    again = _hub(tmp_path, router, server)
    assert again.resume_watches() == 1
    await _settle(again, until=lambda: any(e.kind == "finished" for e in again.state.entries))
    finished = again.state.entries[-1]
    assert (finished.text, finished.session_id) == (
        "brasa finished: It fixed the scanner and all tests pass.",
        BRASA,
    )
    await _settle(again, until=lambda: not again._watches)
    assert HubState("u1", root=tmp_path).watches == {}  # reported, so forgotten
    assert again.resume_watches() == 0


async def test_a_watch_too_old_to_matter_is_dropped_on_resume(tmp_path) -> None:
    HubState("u1", root=tmp_path).keep_watch(
        BRASA, sent_at=time.time() - hub_module.WATCH_MAX_S - 1, name="brasa", user_id="u1"
    )
    hub = _hub(tmp_path, _Router(), _Server())
    assert hub.resume_watches() == 0
    assert HubState("u1", root=tmp_path).watches == {}


async def test_held_files_survive_a_restart(tmp_path) -> None:
    hub = _hub(tmp_path, _Router(), _Server())
    hub.hold_files(REVIEWS)
    await hub.close()
    again = _hub(tmp_path, _Router(), _Server())
    assert again.held_files() == REVIEWS


def test_the_server_resumes_every_hubs_watches_at_startup(tmp_path, monkeypatch) -> None:
    from omnigent.server import hub_chat

    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "_HUBS", {})
    (tmp_path / "morys5").mkdir()
    (tmp_path / "morys5" / "conversation.json").write_text('{"id": "hubconv"}')
    HubState("morys5", root=tmp_path).keep_watch(
        AMARRAS, sent_at=time.time(), name="amarras", user_id="morys5", base_url="http://h:6767"
    )
    registry = DiscussionRegistry()

    async def start():
        resumed = hub_chat.resume_after_restart(
            conversation_store=_ConvStore(), app=None, companions=lambda: registry
        )
        hub = hub_module._HUBS["morys5"]
        running = set(hub._watches)
        await hub.close()
        return resumed, hub, running

    resumed, hub, running = asyncio.run(start())
    assert resumed == 1 and running == {AMARRAS}
    assert (hub.user_id, hub.chat.session_id, hub.chat.base_url) == (
        "morys5",
        "hubconv",
        "http://h:6767",
    )


# -- projects ---------------------------------------------------------------------


async def test_projects_are_newest_first_with_their_newest_chat(tmp_path) -> None:
    server = _Server()
    server.sessions[0] = replace(server.sessions[0], updated_at=50.0)
    server.projects.append(ProjectRecord(id="cccc" + "3" * 28, name="empty", created_at=9.0))
    hub = _hub(tmp_path, _Router(), server)
    projects = await hub.projects()
    assert [(p.name, p.handle, p.chats) for p in projects] == [
        ("amarras", "aaaa", 1),
        ("empty", "cccc", 0),
        ("brasa", "bbbb", 1),
    ]
    assert projects[0].chat.name == "amarras" and projects[0].chat.handle == "aaaa"
    assert projects[1].chat is None
    # The voice reaches each project through its chat, by the project's name.
    assert [s.name for s in await hub.sessions()] == ["amarras", "brasa"]


async def test_the_old_session_list_becomes_projects_once(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    state.set_members([AMARRAS, "loose0", "gone"])
    state.name("loose0", "greetings")
    server = _Server()
    server.projects = []
    server.sessions[0] = replace(server.sessions[0], project_id="")
    server.sessions[1] = replace(server.sessions[1], project_id="")
    hub = _hub(tmp_path, _Router(), server)
    assert sorted(p.name for p in await hub.projects()) == ["Project Analysis", "greetings"]
    assert server.created == [("Project Analysis", AMARRAS), ("greetings", "loose0")]
    # Done once: a project deleted later is not made again.
    server.projects = []
    assert await hub.projects() == []
    assert len(server.created) == 2
    assert HubState("u1", root=tmp_path).moved_to_projects


async def test_a_moved_name_that_is_taken_gets_a_number(tmp_path) -> None:
    state = HubState("u1", root=tmp_path)
    state.set_members(["loose0"])
    state.name("loose0", "Brasa")
    hub = _hub(tmp_path, _Router(), _Server())
    await hub.projects()
    names = [r.name for r in await hub.require_backend().projects()]
    assert names == ["amarras", "brasa", "Brasa 2"]


async def test_projects_are_made_renamed_given_chats_and_deleted(tmp_path) -> None:
    server = _Server()
    hub = _hub(tmp_path, _Router(), server)

    with pytest.raises(ValueError, match="already a project called brasa"):
        await hub.create_project(" Brasa ")
    with pytest.raises(ValueError, match="needs a name"):
        await hub.create_project("  ")
    entry, record = await hub.create_project("robot  arm")
    assert (entry.kind, record.name, entry.link) == (
        "project",
        "robot arm",
        "/?project=robot%20arm",
    )
    assert entry.text.startswith("Created project robot arm. It has no chat yet")

    with pytest.raises(ValueError, match="not one of yours outside every project"):
        await hub.create_project("again", from_chat=AMARRAS)  # already in amarras
    entry, _ = await hub.create_project("hello", from_chat="loose0")
    assert entry.text == "Created project hello, with “Greeting” as its chat."
    assert entry.session_id == "loose0" and entry.link is None

    by_name = {p.name: p for p in await hub.projects()}
    with pytest.raises(ValueError, match="already a project called amarras"):
        await hub.rename_project(by_name["robot arm"], "AMARRAS")
    renamed = await hub.rename_project(by_name["robot arm"], "arm")
    assert renamed.text == "Renamed robot arm to arm."

    server.sessions.append(HubSession(id="loose1", title="Notes"))
    by_name = {p.name: p for p in await hub.projects()}
    with pytest.raises(ValueError):
        await hub.add_chat(by_name["arm"], AMARRAS)
    added = await hub.add_chat(by_name["arm"], "loose1")
    assert added.text == "Moved “Notes” into arm."
    assert {p.name: p.chats for p in await hub.projects()}["arm"] == 1

    deleted = await hub.delete_project({p.name: p for p in await hub.projects()}["arm"])
    assert deleted.text == "Deleted project arm; its chat is kept, outside any project."
    assert "arm" not in {p.name for p in await hub.projects()}
    assert "loose1" in {c.id for c in await hub.candidates()}


async def test_a_new_project_can_be_asked_for_in_the_chat(tmp_path) -> None:
    router = _Router('{"new_project": "robot arm", "say": "Criando."}', '{"new_project": "brasa"}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    hub.submit("cria um projeto robot arm")
    await _settle(hub)
    last = hub.state.entries[-1]
    assert (last.kind, last.link) == ("project", "/?project=robot%20arm")
    assert server.created == [("robot arm", None)]
    hub.submit("cria brasa")
    await _settle(hub)
    assert (
        hub.state.entries[-1].text == "I didn't make it: there is already a project called brasa."
    )


# -- reading sessions from the stores -----------------------------------------


def _msg(role: str, text: str, created_at: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        response_id="r",
        created_at=created_at,
        data={
            "role": role,
            "content": [
                {"type": "output_text" if role == "assistant" else "input_text", "text": text}
            ],
        },
    )


class _ConvStore:
    def __init__(self) -> None:
        now = time.time()
        self.convs = [
            SimpleNamespace(
                id=AMARRAS,
                title="Project Analysis",
                updated_at=now - 60,
                live_status="running",
                workspace="/home/x",
                pending_elicitation_count=0,
            ),
            SimpleNamespace(
                id=BRASA,
                title="Old one",
                updated_at=now - 30 * 24 * 3600,
                live_status="idle",
                workspace="/home/x",
                pending_elicitation_count=0,
            ),
        ]
        self.items = {
            AMARRAS: [
                _msg("user", "look at the petrobras-chains folder", 1.0),
                _msg("assistant", "Organized it.", 2.0),
                _msg("user", "<task-notification>done</task-notification>", 3.0),
                _msg("user", "[System: sub-agent finished]", 4.0),
                _msg("user", "what is the mean error?", 5.0),
                _msg("assistant", "Mean error is 3 mm.", 6.0),
                SimpleNamespace(
                    type="function_call",
                    response_id="r",
                    created_at=7.0,
                    data={"name": "Bash", "arguments": '{"command": "pytest -q"}', "call_id": "c"},
                ),
            ]
        }
        self.listed: list[dict] = []
        #: Project name -> id, for listing a project's chats.
        self.project_ids: dict[str, str] = {}

    def list_conversations(self, **kwargs):
        self.listed.append(kwargs)
        project = kwargs.get("project")
        convs = list(self.convs)
        if project == "":
            convs = [c for c in convs if not getattr(c, "project_id", None)]
        elif project is not None:
            wanted = self.project_ids.get(project)
            convs = [c for c in convs if getattr(c, "project_id", None) == wanted]
        return SimpleNamespace(data=convs)

    def get_conversation(self, session_id):
        return next((c for c in self.convs if c.id == session_id), None)

    def list_items(self, session_id, limit=100, order="asc", type=None):
        items = [i for i in self.items.get(session_id, []) if type is None or i.type == type]
        if order == "desc":
            items = list(reversed(items))
        return SimpleNamespace(data=items[:limit])


def _store_backend(store: _ConvStore) -> HubBackend:
    registry = DiscussionRegistry()
    return hub_module.store_backend(
        store, user_id="morys5", app=None, headers={}, companions=lambda: registry
    )


async def test_store_sessions_say_what_each_is_about() -> None:
    store = _ConvStore()
    sessions = await _store_backend(store).sessions([AMARRAS, "gone"])
    assert [s.id for s in sessions] == [AMARRAS]  # a deleted session is left out
    amarras = sessions[0]
    assert amarras.first_ask == "look at the petrobras-chains folder"
    assert amarras.last_ask == "what is the mean error?"  # notifications are not asks
    assert amarras.said == "Mean error is 3 mm."
    assert amarras.now == "Bash: pytest -q"


async def test_the_listing_is_theirs_newest_first() -> None:
    store = _ConvStore()
    store.convs[1].labels = {"omnigent.wrapper": "codex-native-ui"}
    listed = await _store_backend(store).listing()
    assert [(c.id, c.title, c.provider) for c in listed] == [
        (AMARRAS, "Project Analysis", ""),
        (BRASA, "Old one", "Codex"),
    ]
    assert store.listed[0]["owned_by"] == "morys5"
    assert store.listed[0]["sort_by"] == "updated_at"
    assert store.listed[0]["project"] == ""  # only chats outside every project


async def test_an_archived_session_drops_out_of_the_hub() -> None:
    store = _ConvStore()
    store.convs[0].archived = True
    assert [s.id for s in await _store_backend(store).sessions([AMARRAS, BRASA])] == [BRASA]


async def test_a_turn_reply_is_the_newest_assistant_message_since_the_send() -> None:
    store = _ConvStore()
    backend = _store_backend(store)
    assert (await backend.turn(AMARRAS, 5.5)).reply == "Mean error is 3 mm."
    assert (await backend.turn(AMARRAS, 100.0)).reply == ""


async def test_the_hub_never_offers_itself_or_an_excluded_session() -> None:
    store = _ConvStore()
    now = time.time()
    store.convs.insert(
        0,
        SimpleNamespace(
            id="hubconv",
            title="Hub",
            updated_at=now,
            live_status="idle",
            workspace=None,
            pending_elicitation_count=0,
            labels={"omnigent.hub": "1"},
        ),
    )
    backend = _store_backend(store)
    assert [c.id for c in await backend.listing()] == [AMARRAS, BRASA]
    assert [s.id for s in await backend.sessions(["hubconv", AMARRAS])] == [AMARRAS]
    registry = DiscussionRegistry()
    backend = hub_module.store_backend(
        store,
        user_id="morys5",
        app=None,
        headers={},
        companions=lambda: registry,
        exclude={AMARRAS},
    )
    assert [c.id for c in await backend.listing()] == [BRASA]
    assert await backend.sessions([AMARRAS]) == []


def _projects_app(store: _ConvStore) -> tuple[FastAPI, list[tuple[str, str, object]]]:
    """The projects and session routes the store backend calls, recorded."""
    calls: list[tuple[str, str, object]] = []
    rows: dict[str, dict] = {}
    app = FastAPI()

    @app.get("/v1/projects")
    async def list_projects():
        return {"data": list(rows.values())}

    @app.post("/v1/projects")
    async def create(body: dict):
        pid = f"{len(rows) + 1:032d}"
        rows[pid] = {"id": pid, "name": body["name"], "created_at": 5, "config": body["config"]}
        store.project_ids[body["name"]] = pid
        calls.append(("POST", "/v1/projects", body))
        return rows[pid]

    @app.patch("/v1/projects/{pid}")
    async def rename(pid: str, body: dict):
        calls.append(("PATCH", f"/v1/projects/{pid}", body))
        store.project_ids[body["name"]] = store.project_ids.pop(rows[pid]["name"])
        rows[pid]["name"] = body["name"]
        return rows[pid]

    @app.delete("/v1/projects/{pid}")
    async def delete(pid: str):
        calls.append(("DELETE", f"/v1/projects/{pid}", None))
        store.project_ids.pop(rows.pop(pid)["name"])
        return {"deleted": True}

    @app.patch("/v1/sessions/{sid}")
    async def file(sid: str, body: dict):
        calls.append(("PATCH", f"/v1/sessions/{sid}", body))
        store.get_conversation(sid).project_id = body["project_id"] or None
        return {}

    return app, calls


async def test_store_projects_go_through_the_projects_routes() -> None:
    store = _ConvStore()
    store.convs[0].host_id = "host1"
    store.convs[0].agent_id = "claude-native"
    app, calls = _projects_app(store)
    registry = DiscussionRegistry()
    backend = hub_module.store_backend(
        store, user_id="morys5", app=app, headers={}, companions=lambda: registry
    )

    record = await backend.create_project("amarras", AMARRAS)
    assert (record.name, record.folder, record.chat_ids) == ("amarras", "/home/x", (AMARRAS,))
    # Its new chats start where the chat it was made from runs.
    assert calls[0] == (
        "POST",
        "/v1/projects",
        {
            "name": "amarras",
            "config": {"workspace": "/home/x", "host_id": "host1", "agent_id": "claude-native"},
        },
    )
    assert calls[1] == ("PATCH", f"/v1/sessions/{AMARRAS}", {"project_id": record.id})
    listed = await backend.projects()
    assert [(r.name, r.folder, r.chat_ids) for r in listed] == [("amarras", "/home/x", (AMARRAS,))]
    assert store.listed[-1]["project"] == "amarras"
    assert [c.id for c in await backend.listing()] == [BRASA]

    await backend.rename_project(record.id, "chains")
    assert [r.name for r in await backend.projects()] == ["chains"]
    assert await backend.delete_project(record.id) == 1
    assert calls[-2:] == [
        ("PATCH", f"/v1/sessions/{AMARRAS}", {"project_id": ""}),
        ("DELETE", f"/v1/projects/{record.id}", None),
    ]
    assert await backend.projects() == []
    assert [c.id for c in await backend.listing()] == [AMARRAS, BRASA]


# -- the routes ------------------------------------------------------------------


def test_the_hub_needs_a_signed_in_user(tmp_path) -> None:
    class _NoUser:
        def get_user_id(self, request):
            return None

    app = FastAPI()
    app.include_router(
        create_hub_router(
            auth_provider=_NoUser(), conversation_store=_ConvStore(), artifact_store=object()
        ),
        prefix="/v1",
    )
    with TestClient(app) as tc:
        assert tc.get("/v1/hub").status_code == 401


async def test_a_voice_ask_returns_what_the_hub_said_with_the_call_in_the_prompt(tmp_path) -> None:
    router = _Router('{"send": "bbbb", "message": "Run the tests.", "say": "Mandei pra brasa."}')
    server = _Server()
    hub = _hub(tmp_path, router, server)
    said = await hub.ask("manda ela rodar", via="voice", context="Me: a brasa quebrou\nVoice: Ok.")
    await hub.close()
    assert [(e.kind, e.text) for e in said] == [("sent", "Mandei pra brasa.")]
    assert "[The voice call so far, oldest first]\nMe: a brasa quebrou" in router.prompts[0]
    assert "[They said, aloud, on a voice call]" in router.prompts[0]
    assert server.sent == [(BRASA, "Run the tests.")]


# -- the router process ----------------------------------------------------------


def test_the_router_runs_the_newest_flash_low_agy_lists() -> None:
    listed = [
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium",
        "gemini-3.10-flash-low",
        "gemini-3.10-flash-medium",
        "claude-opus-5-5-low",
        "gemini-3.9-pro-low",
    ]
    assert hub_module.pick_router_model(listed) == "gemini-3.10-flash-low"
    assert hub_module.pick_router_model(["gpt-oss-120b-medium"]) is None


async def test_the_router_model_comes_from_agy_else_the_fallback(monkeypatch) -> None:
    from omnigent.model_fallbacks import SPOKEN_SUMMARY_AGY_DEFAULT_MODEL

    monkeypatch.delenv("OMNIGENT_HUB_MODEL", raising=False)
    monkeypatch.setattr(hub_module, "_listed_model", None)
    asked: list[str] = []

    async def listed(binary):
        asked.append(binary)
        return [{"id": "gemini-3.8-flash-low"}, {"id": "gemini-3.8-flash-medium"}]

    monkeypatch.setattr(hub_module, "_probe_agy_models", listed)
    assert await hub_module.resolve_hub_model() == "gemini-3.8-flash-low"
    assert await hub_module.resolve_hub_model() == "gemini-3.8-flash-low"
    assert len(asked) == 1  # asked once, then remembered

    async def unanswered(binary):
        return None

    monkeypatch.setattr(hub_module, "_listed_model", None)
    monkeypatch.setattr(hub_module, "_probe_agy_models", unanswered)
    assert await hub_module.resolve_hub_model() == SPOKEN_SUMMARY_AGY_DEFAULT_MODEL
    monkeypatch.setenv("OMNIGENT_HUB_MODEL", "pinned-model")
    assert hub_module.hub_model() == "pinned-model"


#: A stand-in for the CLI: answers with the folder it runs in and what it was
#: told, and replies empty (a tool call the CLI refused) when asked to.
_FAKE_ROUTER = """\
import json, os, sys
for line in sys.stdin:
    if not line.strip():
        continue
    content = json.loads(line)["message"]["content"]
    empty = "REACH_FOR_A_TOOL" in content or (
        "Your reply was empty" in content and os.environ.get("FAKE_ROUTER_STUBBORN")
    )
    stall = os.environ.get("FAKE_ROUTER_STALLS")
    again = os.environ.get("FAKE_ROUTER_STALL_ALWAYS")
    if "STALL" in content and stall and (again or not os.path.exists(stall)):
        open(stall, "w").close()
        sys.stdin.readline()  # never answers: a model call that does not come back
    response = "" if empty else json.dumps({"answer": os.getcwd(), "told": content[-300:]})
    sys.stdout.write(json.dumps({"event": "result", "result": {"response": response}}) + "\\n")
    sys.stdout.flush()
"""


@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "HUB_DIR", tmp_path / "hub")
    script = tmp_path / "fake_agy"
    script.write_text(f"#!{sys.executable}\n{textwrap.dedent(_FAKE_ROUTER)}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return HubRouter("hub-test", binary=str(script), model="test-model")


async def test_the_router_starts_in_an_empty_folder_not_the_repo(router, tmp_path) -> None:
    reply = json.loads(await router.decide("which one?", said="which one?", timeout_s=10))
    await router.close()
    folder = tmp_path / "hub" / ".router"
    assert reply["answer"] == str(folder)
    assert list(folder.iterdir()) == []


async def test_an_empty_reply_is_asked_again_in_the_same_process(router) -> None:
    await router.decide("hello", said="hello", timeout_s=10)
    pid = router._process.pid
    reply = json.loads(
        await router.decide("REACH_FOR_A_TOOL", said="was it the audio?", timeout_s=10)
    )
    assert reply["told"] == EMPTY_REPLY_NUDGE
    assert router.running and router._process.pid == pid
    await router.close()


async def test_a_stalled_routing_call_is_asked_again_in_a_fresh_process(
    router, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("FAKE_ROUTER_STALLS", str(tmp_path / "stalled"))
    monkeypatch.setattr(hub_module, "ROUTE_FIRST_TRY_S", 0.5)
    monkeypatch.setattr(hub_module, "ROUTE_RETRY_MIN_S", 0.5)
    reply = json.loads(await router.decide("STALL here", said="cadê o plano?", timeout_s=5))
    assert reply["answer"]  # answered by the second process
    assert (tmp_path / "stalled").exists()
    await router.close()


async def test_a_router_that_stalls_twice_is_unavailable(router, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FAKE_ROUTER_STALLS", str(tmp_path / "stalled"))
    monkeypatch.setenv("FAKE_ROUTER_STALL_ALWAYS", "1")
    monkeypatch.setattr(hub_module, "ROUTE_FIRST_TRY_S", 0.5)
    monkeypatch.setattr(hub_module, "ROUTE_RETRY_MIN_S", 0.5)
    with pytest.raises(DiscussionUnavailable, match="no answer within"):
        await router.decide("STALL here", said="cadê o plano?", timeout_s=1.5)
    assert not router.running


async def test_a_router_that_stays_empty_is_unavailable(router, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_ROUTER_STUBBORN", "1")
    with pytest.raises(DiscussionUnavailable, match="empty answer"):
        await router.decide("REACH_FOR_A_TOOL", said="was it the audio?", timeout_s=10)
    assert not router.running
