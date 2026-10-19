"""HYP-56 part 2: chain class lookup against the REAL network-pallet storage, the
ed25519 registration-key default, and the enclave software measurement pin."""

from __future__ import annotations

import sys
import types

import pytest
from nacl.signing import SigningKey

from talaris_contracts import ed25519_peer_id

from inference_api import chain as chain_mod
from inference_api.config import Settings, settings

HOTKEY = bytes(SigningKey(b"\x05" * 32).verify_key).hex()
PEER_ID = ed25519_peer_id(HOTKEY)
SUBNET_ID = 128_001


class _Q:
    def __init__(self, value):
        self.value = value


class _FakeSubstrate:
    """Records queries; answers like substrate-interface over the real pallet."""

    storage: dict = {}
    queries: list = []

    def __init__(self, url):
        self.url = url

    def query(self, module, item, params):
        type(self).queries.append((module, item, list(params)))
        key = (module, item, tuple(params))
        if key in self.storage:
            return _Q(self.storage[key])
        if item == "PeerIdSubnetNodeId":
            return _Q(0)  # ValueQuery default
        if item in ("SubnetNodesData", "RegisteredSubnetNodesData"):
            return _Q({"id": 0, "classification": {"node_class": "Registered", "start_epoch": 0}})
        raise AssertionError(f"query of storage that does not exist on chain: {module}.{item}")

    def close(self):
        pass


@pytest.fixture
def fake_chain(monkeypatch):
    module = types.ModuleType("substrateinterface")
    module.SubstrateInterface = _FakeSubstrate
    monkeypatch.setitem(sys.modules, "substrateinterface", module)
    monkeypatch.setattr(settings, "chain_subnet_id", SUBNET_ID)
    _FakeSubstrate.storage = {}
    _FakeSubstrate.queries = []
    return _FakeSubstrate


def test_included_node_resolves_via_peer_id_index(fake_chain):
    fake_chain.storage = {
        ("Network", "PeerIdSubnetNodeId", (SUBNET_ID, PEER_ID)): 42,
        ("Network", "SubnetNodesData", (SUBNET_ID, 42)): {
            "id": 42,
            "classification": {"node_class": "Included", "start_epoch": 9},
        },
    }
    node = chain_mod.ChainClient(rpc_url="ws://chain").get_node_by_hotkey(HOTKEY)
    assert node == chain_mod.ChainNode(subnet_node_id=42, classification="Included")
    items = [q[1] for q in fake_chain.queries]
    assert items == ["PeerIdSubnetNodeId", "SubnetNodesData"]
    assert "HotkeySubnetNodeId" not in items and "SubnetNodeClass" not in items


def test_queued_node_is_registered_class(fake_chain):
    fake_chain.storage = {
        ("Network", "PeerIdSubnetNodeId", (SUBNET_ID, PEER_ID)): 7,
        ("Network", "RegisteredSubnetNodesData", (SUBNET_ID, 7)): {
            "id": 7,
            "classification": {"node_class": {"Registered": None}, "start_epoch": 1},
        },
    }
    node = chain_mod.ChainClient(rpc_url="ws://chain").get_node_by_hotkey(HOTKEY)
    assert node.subnet_node_id == 7 and node.classification == "Registered"
    assert not chain_mod.class_meets_minimum(node.classification, "Included")


def test_unregistered_peer_resolves_to_no_node(fake_chain):
    node = chain_mod.ChainClient(rpc_url="ws://chain").get_node_by_hotkey(HOTKEY)
    assert node == chain_mod.ChainNode(subnet_node_id=None, classification=None)
    assert not chain_mod.class_meets_minimum(node.classification, "Included")


def test_lookup_requires_subnet_id_and_ed25519_key(fake_chain, monkeypatch):
    with pytest.raises(chain_mod.ChainReadError):
        chain_mod.ChainClient(rpc_url="ws://chain").get_node_by_hotkey("0x" + "ab" * 20)
    monkeypatch.setattr(settings, "chain_subnet_id", None)
    with pytest.raises(chain_mod.ChainConfigError):
        chain_mod.ChainClient(rpc_url="ws://chain").get_node_by_hotkey(HOTKEY)


def test_registration_key_type_defaults_to_ed25519(monkeypatch):
    monkeypatch.delenv("REGISTRATION_KEY_TYPE", raising=False)
    assert Settings.model_fields["registration_key_type"].default == "ed25519"


def test_measurement_pin_settings_reach_the_shared_policy(monkeypatch):
    from inference_api.provisioner import service

    monkeypatch.setattr(settings, "allow_dev_attestation", False)
    monkeypatch.setattr(settings, "intel_tdx_measurements", '[{"mrtd": "' + "ab" * 48 + '", "label": "v1"}]')
    monkeypatch.setattr(settings, "intel_require_measurement_pin", True)
    expected = service.build_expected_claims(expected_miner_hash="aa" * 32)
    assert expected.require_measurement_pin is True
    assert expected.tdx_measurement_allowlist[0].label == "v1"

    monkeypatch.setattr(settings, "intel_tdx_measurements", None)
    monkeypatch.setattr(settings, "intel_require_measurement_pin", False)
    expected = service.build_expected_claims(expected_miner_hash="aa" * 32)
    assert expected.tdx_measurement_allowlist is None
    assert expected.require_measurement_pin is False


def test_malformed_measurement_allowlist_fails_at_startup(monkeypatch):
    monkeypatch.setenv("INTEL_TDX_MEASUREMENTS", '[{"mrtd": "short"}]')
    with pytest.raises(Exception):
        Settings()
