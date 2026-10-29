"""External enrollment never grants another host's identity or provider credentials."""

import pytest

from omnigent.db.utils import now_epoch
from omnigent.stores.host_store import HostStore

HOST = "9eef91528c694216bcc7954112cd6856"
OTHER = "8661c0d6534c4314923985bdaa550a10"


def test_enrollment_reconnect_rotation_revocation_and_local_host(db_uri):
    store = HostStore(db_uri)
    store.upsert_on_connect(OTHER, "nexus", "owner")
    store.enroll_external_host(HOST, "hipolito", "owner", "first", now_epoch() + 100)
    assert store.resolve_launch_token(OTHER, "first") is None
    assert store.resolve_launch_token(HOST, "wrong") is None
    assert store.resolve_launch_token(HOST, "first").sandbox_provider is None
    for _ in range(2):
        store.upsert_on_connect(HOST, "hipolito", "owner", managed_token="first")
        store.set_offline(HOST)
    store.enroll_external_host(HOST, "hipolito", "owner", "second", now_epoch() + 100)
    assert store.resolve_launch_token(HOST, "first") is None
    assert store.resolve_launch_token(HOST, "second") is not None
    store.revoke_launch_token(HOST)
    assert store.resolve_launch_token(HOST, "second") is None
    assert store.get_host(OTHER).status == "online"


def test_enrollment_refuses_cross_owner_duplicate_name_and_expiry(db_uri):
    store = HostStore(db_uri)
    store.upsert_on_connect(OTHER, "nexus", "owner")
    with pytest.raises(ValueError):
        store.enroll_external_host(OTHER, "stolen", "attacker", "x", now_epoch() + 100)
    with pytest.raises(ValueError):
        store.enroll_external_host(HOST, "nexus", "owner", "x", now_epoch() + 100)
    store.enroll_external_host(HOST, "hipolito", "owner", "expired", now_epoch() - 1)
    assert store.resolve_launch_token(HOST, "expired") is None
    with pytest.raises(ValueError):
        store.upsert_on_connect(HOST, "hipolito", "owner", managed_token="expired")
