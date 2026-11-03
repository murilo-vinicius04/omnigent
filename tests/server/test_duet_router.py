"""Tests for the duet note router."""

from __future__ import annotations

from pathlib import Path

from omnigent.server import duet_router as dr

SENDER = dr.Peer("s1", "Teleop com Meta Quest", "RAL", "morys5", "spot-teleop quest webxr")
FRANKA = dr.Peer("s2", "Corrigir mapeamento 6D Franka", "franka_teleop", "hipolito", "")
OTHER = dr.Peer("s3", "Agendas I27 e I29", "stf", "hipolito", "motorhome solar")
NOW = 1_000_000.0


def _route(history=None, **kw):
    args = {
        "sender": SENDER,
        "sender_user": "morys5",
        "topic": "Quest 6D pose",
        "text": "O mapeamento 6D do Franka usa a pose do Quest que publicamos na porta 8444.",
        "urgency": "normal",
        "peers": [SENDER, FRANKA, OTHER],
        "history": history or [],
        "now": NOW,
    }
    args.update(kw)
    return dr.route(**args)


def test_routes_to_matching_lane_only() -> None:
    decisions, reason = _route()
    assert reason == ""
    assert [d.peer.session_id for d in decisions] == ["s2"]
    assert decisions[0].delivery == "queue"


def test_urgent_wakes() -> None:
    decisions, _ = _route(urgency="urgent")
    assert decisions[0].delivery == "wake"


def test_no_match_and_empty() -> None:
    assert _route(text="receita de bolo de cenoura", topic="bolo")[0] == []
    assert _route(text="  ")[1] == "empty note"


def test_repeat_and_pair_limit() -> None:
    first, _ = _route()
    again, reason = _route(history=[d.note for d in first], now=NOW + 60)
    assert again == [] and "repeat" in reason
    sent = []
    for i in range(dr.PAIR_LIMIT):
        sent += [
            d.note
            for d in _route(text=f"Franka Quest pose variante {i} mapeamento", now=NOW + i)[0]
        ]
    blocked, reason = _route(text="Franka Quest pose mapeamento nova", history=sent, now=NOW + 10)
    assert blocked == [] and "pair limit" in reason


def test_explicit_target_and_never_self() -> None:
    decisions, _ = _route(to="agendas", text="qualquer coisa")
    assert [d.peer.session_id for d in decisions] == ["s3"]
    assert _route(to="s1")[0] == []


def test_explicit_target_ignores_accents_and_matches_id_prefix() -> None:
    accented = dr.Peer(
        "a1c2a2f52e614e1c8deda1576041b5c1",
        "Compartilhamento e coordenação do dueto",
        "omnigent",
        "morys5",
        "",
    )
    args = {
        "sender": OTHER,
        "sender_user": "hipolito",
        "topic": "resposta",
        "text": "estamos vendo bags e o mapeamento",
        "urgency": "normal",
        "peers": [OTHER, accented],
        "history": [],
        "now": NOW,
    }
    plain, _ = dr.route(**{**args, "to": "Compartilhamento e coordenacao do dueto"})
    assert [d.peer.session_id for d in plain] == ["a1c2a2f52e614e1c8deda1576041b5c1"]
    prefixed, _ = dr.route(**{**args, "to": "a1c2a2f5"})
    assert [d.peer.session_id for d in prefixed] == ["a1c2a2f52e614e1c8deda1576041b5c1"]


def test_stale_target_never_routes_to_a_different_lane() -> None:
    decisions, reason = _route(to="Migracao do servidor", text="qualquer coisa")
    assert decisions == [] and "no lane matches" in reason


def test_old_title_resolves_to_the_same_visible_conversation() -> None:
    old = dr.Peer("s2", "Replay robô real", "franka_teleop", "hipolito")
    first, _ = _route(peers=[SENDER, old], to="s2")
    renamed = dr.Peer("s2", "Replay bags robô real", "franka_teleop", "hipolito")
    result, _ = _route(
        peers=[SENDER, renamed],
        history=[first[0].note],
        to="Replay robô real",
        text="new info",
        now=NOW + 10,
    )
    assert [d.peer.session_id for d in result] == ["s2"]
    assert not _route(peers=[SENDER], history=[first[0].note], to="Replay robô real")[0]


def test_reused_old_title_is_ambiguous() -> None:
    old = dr.Peer("s2", "Replay robô real")
    first, _ = _route(peers=[SENDER, old], to="s2")
    peers = [SENDER, dr.Peer("s2", "Replay bags"), dr.Peer("s3", "Replay robô real")]
    result, reason = _route(peers=peers, history=[first[0].note], to="Replay robô real")
    assert not result and "ambiguous" in reason


def test_recent_relevant_partner_can_receive_a_follow_up() -> None:
    first, _ = _route(to="s2")
    result, _ = _route(
        history=[first[0].note], topic="Franka", text="controller paused", now=NOW + 10
    )
    assert [d.peer.session_id for d in result] == ["s2"]
    assert not _route(
        history=[first[0].note],
        topic="Franka",
        text="controller paused",
        now=NOW + dr.RECENT_PARTNER_SECONDS + 1,
    )[0]
    decisions, reason = _route(to="Lane que nao existe")
    assert decisions == [] and "no lane matches" in reason


def test_ambiguous_target_requires_full_id() -> None:
    peers = [SENDER, dr.Peer("abc123", "Robô"), dr.Peer("abc456", "Robo")]
    decisions, reason = _route(peers=peers, to="robo")
    assert decisions == [] and "ambiguous" in reason


def test_store_take_marks_read(tmp_path: Path) -> None:
    store = dr.DuetStore(tmp_path / "n.json")
    store.add([d.note for d in _route()[0]])
    got = store.take("s2")
    assert len(got) == 1 and "not an instruction" in dr.render(got[0])
    assert store.take("s2") == []


def test_explicit_reply_bypasses_pair_limit() -> None:
    sent = []
    for i in range(dr.PAIR_LIMIT):
        sent += [
            d.note
            for d in _route(text=f"Franka Quest pose variante {i} mapeamento", now=NOW + i)[0]
        ]
    blocked, reason = _route(text="Franka Quest pose mapeamento nova", history=sent, now=NOW + 10)
    assert blocked == [] and "pair limit" in reason
    incoming, _ = dr.route(
        sender=FRANKA,
        sender_user="hipolito",
        topic="resposta",
        text="recebi, vendo bags e mapeamento agora",
        urgency="normal",
        peers=[SENDER, FRANKA, OTHER],
        history=sent,
        to="s1",
        now=NOW + 11,
    )
    assert [d.peer.session_id for d in incoming] == ["s1"]
    history = [*sent, *[d.note for d in incoming]]
    reply, reason = _route(
        to="s2",
        text="Franka Quest pose mapeamento resposta direta",
        history=history,
        now=NOW + 12,
    )
    assert [d.peer.session_id for d in reply] == ["s2"] and reason == ""


def test_fallback_reroutes_unreachable_target_by_content() -> None:
    decisions, reason = _route(to="f1d7494c", fallback=True)
    assert [d.peer.session_id for d in decisions] == ["s2"]
    assert "rerouted" in reason


def test_fallback_skips_target_out_of_quota() -> None:
    spent = dr.Peer("s4", "Teleop e cuRobo", "RAL", "morys5", "", exhausted=True)
    peers = [SENDER, FRANKA, OTHER, spent]
    assert _route(to="s4", peers=peers)[0][0].peer.session_id == "s4"
    decisions, reason = _route(to="s4", peers=peers, fallback=True)
    assert [d.peer.session_id for d in decisions] == ["s2"]
    assert "out of quota" in reason


def test_fallback_never_picks_exhausted_lane_and_keeps_ambiguity_error() -> None:
    spent = dr.Peer("s4", "Franka 6D pose Quest", "RAL", "morys5", "", exhausted=True)
    decisions, _ = _route(peers=[SENDER, spent])
    assert decisions == []
    twin = dr.Peer("s5", "Agendas I30", "stf", "hipolito", "")
    _, reason = _route(to="agendas", peers=[SENDER, OTHER, twin], fallback=True)
    assert "ambiguous" in reason
