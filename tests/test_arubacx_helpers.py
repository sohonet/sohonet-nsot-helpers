"""Tests for the AOS-CX napalm patches used by the Nornir interface SSoT.

The payload shapes below are trimmed from real AOS-CX 10.09 REST responses
(GET system/interfaces?depth=2&attributes=...). Run with: python -m pytest tests/
Needs napalm, napalm-aruba-cx and pyaoscx importable (e.g. inside the Nautobot image).
"""

import pytest
import requests
from napalm.base.exceptions import ConnectionException

from sohonet_nsot_helpers.napalm import arubacx_helpers as h

BASE = "https://sw/rest/v10.09/"

BULK = {
    "1/1/25": {"name": "1/1/25", "admin_state": "up", "admin": "up", "link_state": "up", "mtu": 1500,
               "description": "uplink a", "hw_intf_info": {"max_speed": 25000, "mac_addr": "aa"},
               "interfaces": {"1/1/25": BASE + "system/interfaces/1%2F1%2F25"}, "type": "system",
               "ip4_address": None, "ip6_addresses": BASE + "system/interfaces/1%2F1%2F25/ip6_addresses",
               "ip6_address_link_local": {}, "ip6_autoconfigured_addresses": {}},
    "1/1/26": {"name": "1/1/26", "admin_state": "down", "admin": "up", "link_state": "down", "mtu": 1500,
               "description": None, "hw_intf_info": {"max_speed": 25000}, "interfaces": {}, "type": "system"},
    # LAGs have no admin_state / hw_intf_info / mtu; members are keyed by name.
    "lag10": {"name": "lag10", "admin": "up", "description": "CC::Po8", "type": "lag",
              "interfaces": {"1/1/25": BASE + "system/interfaces/1%2F1%2F25",
                             "1/1/26": BASE + "system/interfaces/1%2F1%2F26"},
              "ip4_address": None, "ip6_addresses": BASE + "system/interfaces/lag10/ip6_addresses",
              "ip6_address_link_local": {}, "ip6_autoconfigured_addresses": {}},
    # Management VLAN: ip6_addresses is a URI *string* on this firmware.
    "vlan707": {"name": "vlan707", "admin_state": "up", "link_state": "up", "mtu": None, "description": "DCN-IF",
                "hw_intf_info": {"bridge": False}, "type": "vlan", "interfaces": {"vlan707": BASE + "x"},
                "ip4_address": "10.32.82.239/23",
                "ip6_addresses": BASE + "system/interfaces/vlan707/ip6_addresses",
                "ip6_address_link_local": {"fe80::1/64": BASE + "y"},
                "ip6_autoconfigured_addresses": BASE + "system/interfaces/vlan707/ip6_autoconfigured_addresses"},
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, verify=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.routes[url.replace(BASE, "")](params)

    def post(self, url, **kw):
        self.calls.append((url, None, kw.get("timeout")))
        return FakeResponse({})

    def close(self):
        pass


class FakeDriver:
    hostname = "sw"

    def __init__(self, routes):
        self.session = FakeSession(routes)
        self.session_info = {"s": self.session, "url": BASE}
        self.optional_args = {}
        self._sohonet_bulk_interfaces = None


def _driver(bulk=BULK):
    return FakeDriver({"system/interfaces": lambda params: FakeResponse(bulk)})


def test_get_interfaces_is_one_request_and_reused_by_get_interfaces_ip():
    drv = _driver()
    h.aoscx_get_interfaces(drv)
    h.aoscx_get_interfaces_ip(drv)
    gets = [c for c in drv.session.calls if c[0] == BASE + "system/interfaces"]
    assert len(gets) == 1
    url, params, timeout = gets[0]
    assert params["depth"] == 2 and "ip4_address" in params["attributes"]
    assert timeout == h.AOSCX_BULK_TIMEOUT


def test_lag_members_come_from_rest_keys_without_cli():
    # No `device` attribute => CLI fallbacks cannot run; REST alone must find members.
    result = h.aoscx_get_interfaces(_driver())
    assert result["lag 10"]["children"] == ["1/1/25", "1/1/26"]


def test_lag_members_from_legacy_uri_list():
    bulk = dict(BULK, lag10=dict(BULK["lag10"], interfaces=[BASE + "system/interfaces/1%2F1%2F49"]))
    assert h.aoscx_get_interfaces(_driver(bulk))["lag 10"]["children"] == ["1/1/49"]


def test_lag_enabled_falls_back_to_admin():
    result = h.aoscx_get_interfaces(_driver())
    assert result["lag 10"]["is_enabled"] is True
    # Physical ports keep using admin_state (existing semantics).
    assert result["1/1/26"]["is_enabled"] is False


def test_names_normalised():
    result = h.aoscx_get_interfaces(_driver())
    assert "vlan 707" in result and "lag 10" in result and "vlan707" not in result


def test_interfaces_ip_keeps_ipv4_when_ip6_is_a_uri_string():
    ips = h.aoscx_get_interfaces_ip(_driver())
    assert ips["vlan 707"]["ipv4"] == {"10.32.82.239": {"prefix_length": 23}}
    assert ips["vlan 707"]["ipv6"] == {"fe80::1": {"prefix_length": 64}}
    assert "1/1/25" not in ips  # URI string only, no addresses


def test_bulk_rejected_falls_back_to_per_interface(monkeypatch):
    drv = FakeDriver({"system/interfaces": lambda params: FakeResponse({}, status=400)})
    import pyaoscx.interface as pi
    monkeypatch.setattr(pi, "get_all_interface_names", lambda **kw: ["1/1/25"])
    monkeypatch.setattr(pi, "get_interface", lambda name, depth=0, **kw: BULK[name])
    assert h._bulk_interfaces(drv) == {"1/1/25": BULK["1/1/25"]}


def test_timeout_session_default_and_floor(monkeypatch):
    seen = []
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, **kw: seen.append(kw["timeout"]))
    s = h._TimeoutSession()
    s.get("https://x/")              # pyaoscx calls with no timeout
    s.get("https://x/", timeout=3)   # pyaoscx's hard-coded 3s
    s.get("https://x/", timeout=120)
    assert seen == [h.AOSCX_REST_TIMEOUT, h.AOSCX_REST_MIN_TIMEOUT, 120]


@pytest.mark.parametrize("outcome", ["http401", "timeout"])
def test_login_raises_connection_exception_not_systemexit(monkeypatch, outcome):
    def fake_post(self, url, **kw):
        if outcome == "timeout":
            raise requests.exceptions.ReadTimeout("slow")
        return FakeResponse({}, status=401)

    monkeypatch.setattr(h._TimeoutSession, "post", fake_post)
    with pytest.raises(ConnectionException):
        h.aoscx_rest_login(BASE, "u", "p")


def test_open_logs_out_rest_when_ssh_fails(monkeypatch):
    logged_out = []

    def fake_orig_open(self):
        self.session_info = {"s": "S", "url": BASE}
        raise ConnectionException("Cannot connect to switch via SSH")

    monkeypatch.setattr(h, "_orig_open", fake_orig_open)
    monkeypatch.setattr(h, "aoscx_rest_logout", lambda **kw: logged_out.append(kw))
    drv = _driver()
    drv.isAlive = True
    with pytest.raises(ConnectionException):
        h.aoscx_open(drv)
    assert logged_out == [{"s": "S", "url": BASE}]
    assert drv.session_info == {} and drv.isAlive is False


def test_patches_installed():
    from napalm_aoscx.aoscx import AOSCXDriver
    from pyaoscx import session
    assert AOSCXDriver.open is h.aoscx_open
    assert AOSCXDriver.get_facts is h.aoscx_get_facts
    assert session.login is h.aoscx_rest_login
