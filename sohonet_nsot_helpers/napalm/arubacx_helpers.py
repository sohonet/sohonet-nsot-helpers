# sohonet_nsot_helpers/napalm/arubacx_helpers.py

import logging
import os
import re
from urllib.parse import unquote

import requests

_log = logging.getLogger(__name__)

# --- REST timeouts ---------------------------------------------------------
# pyaoscx 1.0 hard-codes very short timeouts (2-5s, e.g. timeout=3 on every
# per-interface GET) and makes several calls with no timeout at all (logout,
# get_all_interface_names, get_system_info, get_product_info). Measured on the
# estate: per-interface GETs for ports with LLDP/PoE neighbours take ~4s, so the
# 3s limit fails the whole host; a call with no timeout can hang a Nornir worker
# thread until Celery's hard time limit. Every pyaoscx session we create
# enforces a default and a floor instead.
AOSCX_REST_TIMEOUT = float(os.environ.get("AOSCX_REST_TIMEOUT", "30"))
AOSCX_REST_MIN_TIMEOUT = float(os.environ.get("AOSCX_REST_MIN_TIMEOUT", "15"))
# The one bulk interfaces request replaces ~100 small ones; on CX6100s it took
# 7s idle and >30s while other sessions were busy, so give it its own budget.
AOSCX_BULK_TIMEOUT = float(os.environ.get("AOSCX_BULK_TIMEOUT", "120"))

# Attributes the SSoT reads. Fetched for every interface in ONE request
# (GET system/interfaces?depth=2&attributes=...) instead of one depth=1 GET per
# interface, which costs ~100 sequential requests per switch and is ~100x
# slower (CX6300: 0.3s vs ~25s; CX6100: 0.9s vs 147s, some ports never answer).
# depth=2 on the collection returns exactly what depth=1 returns per interface.
_BULK_ATTRIBUTES = (
    "name", "description", "admin_state", "admin", "link_state", "mtu", "hw_intf_info", "interfaces", "type",
    "ip4_address", "ip6_addresses", "ip6_address_link_local", "ip6_autoconfigured_addresses",
)


class _TimeoutSession(requests.Session):
    """requests.Session that never issues a request without a sane timeout."""

    def request(self, method, url, **kwargs):
        timeout = kwargs.get("timeout")
        if timeout is None:
            kwargs["timeout"] = AOSCX_REST_TIMEOUT
        elif isinstance(timeout, (int, float)):
            kwargs["timeout"] = max(timeout, AOSCX_REST_MIN_TIMEOUT)
        return super().request(method, url, **kwargs)


def aoscx_rest_login(base_url, username=None, password=None):
    """
    Replacement for pyaoscx.session.login.

    The stock login uses a 5s timeout and calls exit(-1) on any failure. That
    SystemExit is a BaseException, so Nornir doesn't treat it as a host failure
    and it can take down the worker; it also hides why the login failed. Raise
    napalm's ConnectionException with the reason instead.
    """
    from napalm.base.exceptions import ConnectionException

    s = _TimeoutSession()
    try:
        response = s.post(base_url + "login", data={"username": username, "password": password}, verify=False)
    except requests.exceptions.RequestException as e:
        s.close()
        raise ConnectionException(f"AOS-CX REST login to {base_url} failed: {type(e).__name__}: {e}") from e
    if response.status_code != 200:
        s.close()
        raise ConnectionException(
            f"AOS-CX REST login to {base_url} failed: HTTP {response.status_code}: {response.text[:200]}"
        )
    return s


def aoscx_rest_logout(**kwargs):
    """Replacement for pyaoscx.session.logout: bounded, never raises, always closes the socket pool."""
    s, url = kwargs.get("s"), kwargs.get("url")
    if s is None or not url:
        return False
    try:
        return s.post(url + "logout", verify=False, timeout=10).status_code == 200
    except Exception:
        return False
    finally:
        s.close()


def _bulk_interfaces(self):
    """
    All interfaces with the attributes the SSoT needs, from ONE REST request.

    Cached on the driver instance so get_interfaces and get_interfaces_ip share
    it (napalm_get calls them back to back on the same connection).
    """
    cached = getattr(self, "_sohonet_bulk_interfaces", None)
    if cached is not None:
        return cached
    try:
        response = self.session_info["s"].get(
            self.session_info["url"] + "system/interfaces",
            params={"depth": 2, "attributes": ",".join(_BULK_ATTRIBUTES)},
            verify=False,
            timeout=AOSCX_BULK_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected system/interfaces payload type {type(data).__name__}")
    except (requests.exceptions.HTTPError, ValueError) as e:
        # The switch rejected the bulk query (as opposed to timing out): fall
        # back to the old one-GET-per-interface path, now with sane timeouts.
        _log.warning('AOS-CX bulk interfaces query failed on %s (%s); falling back to per-interface GETs',
                     getattr(self, 'hostname', '?'), e)
        from pyaoscx import interface as pyaoscx_interface
        data = {
            name: pyaoscx_interface.get_interface(name, depth=1, **self.session_info)
            for name in pyaoscx_interface.get_all_interface_names(**self.session_info)
        }
    self._sohonet_bulk_interfaces = data
    return data


# REST-only mode: skip the driver's SSH (use_cli) session. The SSH half of
# open() costs 5-15s per switch (login, session prep, two warm-up commands)
# and, for the interface SSoT, adds nothing: across 54 switches / 15 models the
# REST-only get_interfaces output was identical. It is process-wide because
# napalm builds the driver for us; jobs flip it with `rest_only()` around their
# Nornir run (Celery prefork runs one job per process at a time). Drivers used
# for CLI work (get_config etc.) are unaffected unless a job opts in.
_REST_ONLY = False

# Highest REST API version the driver is written against; pyaoscx 1.0 routes
# every non-v1 URL down the same v10.04-compatible path.
_PREFERRED_REST_VERSION = (10, 9)
_MIN_REST_VERSION = (10, 4)


class rest_only:
    """Context manager: AOS-CX drivers opened inside it don't open SSH."""

    def __enter__(self):
        global _REST_ONLY
        self._prev, _REST_ONLY = _REST_ONLY, True
        return self

    def __exit__(self, *exc):
        global _REST_ONLY
        _REST_ONLY = self._prev
        return False


def _negotiate_base_url(self):
    """
    Pick the newest REST version the switch serves, up to v10.09.

    The driver hard-codes /rest/v10.09/; firmware older than 10.09 answers the
    login with HTTP 404 (seen on JL676A at v10.04 and R8N88A at v10.08), so
    those switches never synced. GET /rest is unauthenticated and lists the
    versions on offer. Returns True if base_url changed.
    """
    try:
        offered = requests.get(f"https://{self.hostname}/rest", verify=False, timeout=AOSCX_REST_TIMEOUT).json()
    except (requests.exceptions.RequestException, ValueError):
        return False
    usable = []
    for name in offered:
        m = re.match(r'^v(\d+)\.(\d+)$', name)
        if m:
            version = (int(m.group(1)), int(m.group(2)))
            if _MIN_REST_VERSION <= version <= _PREFERRED_REST_VERSION:
                usable.append(version)
    if not usable:
        return False
    major, minor = max(usable)
    new_url = "https://{0}/rest/v{1}.{2:02d}/".format(self.hostname, major, minor)
    if new_url == self.base_url:
        return False
    _log.info('AOS-CX %s: REST v%d.%02d not served, using v%d.%02d',
              self.hostname, *_PREFERRED_REST_VERSION, major, minor)
    self.base_url = new_url
    return True


def aoscx_open(self):
    """
    Wrapper for AOSCXDriver.open().

    - REST-only mode (see rest_only) skips the SSH session.
    - Negotiates the REST API version if the switch doesn't serve v10.09.
    - open() logs in to REST first, then opens SSH. If SSH fails it raises
      without logging out of REST, and Nornir never calls close() on a
      connection that failed to open, so the REST session leaks on the switch
      until it idles out. AOS-CX caps concurrent REST sessions, so leaked
      sessions make later logins fail. Log out before re-raising.
    """
    from napalm.base.exceptions import ConnectionException

    self._sohonet_bulk_interfaces = None
    if _REST_ONLY:
        # The driver forces use_cli=True in __init__; optional_args may be the
        # shared dict from PLUGINS_CONFIG, so copy before changing it.
        self.optional_args = dict(self.optional_args, use_cli=False)
    try:
        try:
            _orig_open(self)
        except ConnectionException as e:
            if "HTTP 404" not in str(e) or not _negotiate_base_url(self):
                raise
            _orig_open(self)
    except BaseException:
        if self.session_info:
            aoscx_rest_logout(**self.session_info)
            self.session_info = {}
        self.isAlive = False
        raise


def aoscx_close(self):
    """
    Safe replacement for AOSCXDriver.close().

    The stock close() calls session.logout(**self.session_info) then
    self.device.disconnect(). If open() failed before the REST login
    completed, session_info is {} and logout(**{}) raises KeyError,
    killing the billiard worker process. Guard both calls.
    """
    if self.session_info.get('s') and self.session_info.get('url'):
        aoscx_rest_logout(**self.session_info)
    self.session_info = {}
    self._sohonet_bulk_interfaces = None
    self.isAlive = False
    if self.optional_args.get('use_cli') and hasattr(self, 'device'):
        try:
            self.device.disconnect()
        except Exception:
            pass


def aoscx_get_interfaces(self):
    """
    Monkeypatch for AOSCXDriver.get_interfaces.

    Replicates the original driver logic and adds:
      - children: list of physical member port names for LAG interfaces
      - ve_children: empty list (Aruba CX has no VE parent concept)
      - type: None (media type determined later by interface_type() helper)
      - mtu: always an int (0 when unavailable)

    LAG member discovery strategy:
      1. REST API at depth=1 — expands the 'interfaces' dict on each LAG
         so member port names can be read from the URI keys/values. Covers
         both LACP and static LAGs on firmware that stores LAG membership
         in the Interface table (v10.04+).
      2. CLI fallback via 'show lacp interfaces' — always runs; creates
         LAG entries for any LAGs discovered in CLI that were absent from
         the Interface table (v1 API firmware stores LAGs in Port table).

    VLAN interface naming:
      Aruba CX returns 'vlan707'; Nautobot stores 'vlan 707'. Names are
      normalised before returning.

    OOB management port:
      pyaoscx get_all_interface_names() omits 'mgmt'. It is fetched
      explicitly and added to the result.
    """
    from pyaoscx import interface as pyaoscx_interface

    interfaces_return = {}
    bulk = _bulk_interfaces(self)

    for name, iface in bulk.items():
        iface = iface or {}
        description = iface.get('description', '') or ''
        hw_info = iface.get('hw_intf_info', {}) or {}
        try:
            # selftest_speed reflects the installed transceiver's speed (e.g. 25G SFP28
            # in a 50G-capable port); max_speed reflects the port's hardware ceiling.
            # Use selftest_speed when present so interface_type() maps to the right slug.
            raw_speed = (
                hw_info.get('selftest_speed') or hw_info.get('max_speed')
                if isinstance(hw_info, dict) else None
            )
            speed = int(raw_speed or 0)
        except (ValueError, TypeError):
            speed = 0
        try:
            mtu = int(iface.get('mtu') or 0)
        except (ValueError, TypeError):
            mtu = 0
        mac = (hw_info.get('mac_addr') or '') if isinstance(hw_info, dict) else ''
        if speed == 0 and isinstance(hw_info, dict) and hw_info:
            _log.debug('hw_intf_info for %s (speed=0): %s', name, hw_info)

        interfaces_return[name] = {
            'is_up': iface.get('link_state') == 'up',
            # LAGs have no admin_state, only the configured 'admin'; without this
            # fallback every LAG synced as disabled (Nautobot holds 252/254 LAGs
            # enabled). Limited to LAGs: port-access personas ('access', 'WAP')
            # also lack admin_state but are held as disabled, so leave them be.
            'is_enabled': (iface.get('admin_state') == 'up'
                           or (iface.get('type') == 'lag' and iface.get('admin') == 'up')),
            'description': description,
            'last_flapped': -1.0,
            'speed': speed,
            'mtu': mtu,
            'mac_address': mac,
            'children': [],
            've_children': [],
            'type': None,
        }

    # OOB management port: try REST first, fall back to CLI, then unconditional stub.
    # pyaoscx returns 404 for 'mgmt' on most firmware versions; 'show interface mgmt'
    # also returns empty on some firmware.  The port always exists — add it regardless.
    if 'mgmt' not in interfaces_return:
        _mgmt_added = False
        try:
            iface = pyaoscx_interface.get_interface('mgmt', depth=1, **self.session_info)
            hw_info = iface.get('hw_intf_info', {}) or {}
            try:
                speed = int(hw_info.get('max_speed') or 0) if isinstance(hw_info, dict) else 0
            except (ValueError, TypeError):
                speed = 0
            try:
                mtu = int(iface.get('mtu') or 0)
            except (ValueError, TypeError):
                mtu = 0
            interfaces_return['mgmt'] = {
                'is_up': iface.get('link_state') == 'up',
                'is_enabled': iface.get('admin_state') == 'up',
                'description': iface.get('description', '') or '',
                'last_flapped': -1.0,
                'speed': speed,
                'mtu': mtu,
                'mac_address': (hw_info.get('mac_addr') or '') if isinstance(hw_info, dict) else '',
                'children': [],
                've_children': [],
                'type': None,
                'management': True,
            }
            _mgmt_added = True
        except Exception:
            pass

        if not _mgmt_added and hasattr(self, 'device'):
            try:
                out = self.device.send_command('show interface mgmt')
                _log.debug('show interface mgmt output:\n%s', out)
                if out and 'invalid' not in out.lower():
                    is_up = bool(re.search(r'Admin State\s*:\s*up', out, re.IGNORECASE))
                    interfaces_return['mgmt'] = {
                        'is_up': is_up,
                        'is_enabled': True,
                        'description': '',
                        'last_flapped': -1.0,
                        'speed': 0,
                        'mtu': 0,
                        'mac_address': '',
                        'children': [],
                        've_children': [],
                        'type': None,
                        'management': True,
                    }
                    _mgmt_added = True
            except Exception as e:
                _log.debug('show interface mgmt failed: %s', e)


    # Normalise VLAN interface names: 'vlan707' → 'vlan 707'
    for old in list(interfaces_return):
        if re.match(r'^vlan\d+$', old, re.IGNORECASE):
            new = re.sub(r'^(vlan)(\d+)$', r'\1 \2', old, flags=re.IGNORECASE)
            if old != new:
                interfaces_return[new] = interfaces_return.pop(old)

    # --- REST API: LAG member discovery from the bulk payload's 'interfaces'
    # attribute (v10.04+ firmware keeps LAGs in the Interface table).
    lag_names = [n for n in interfaces_return if re.match(r'^lag\d+$', n, re.IGNORECASE)]
    for lag_name in lag_names:
        try:
            interfaces_field = (bulk.get(lag_name) or {}).get('interfaces') or {}
            # v10.x returns {"1/1/25": "/rest/v10.09/system/interfaces/1%2F1%2F25"};
            # older payloads were a list of URIs. The member name is the dict key,
            # or the last URI segment (percent-decoded). Previously the key itself
            # was split on '/', yielding '25', so REST never found members and
            # LAG membership depended entirely on the (flaky under load) CLI.
            entries = (interfaces_field.items() if isinstance(interfaces_field, dict)
                       else ((None, uri) for uri in interfaces_field))
            children = []
            for key, uri in entries:
                candidates = [key, unquote(str(uri or '').rstrip('/').split('/')[-1])]
                port_name = next((c for c in candidates
                                  if c and re.match(r'^\d+/\d+/\d+$', c)), None)
                if port_name:
                    children.append(port_name)
            if children:
                interfaces_return[lag_name]['children'] = sorted(children)
        except Exception:
            pass

    # --- CLI: discover LAGs and members.
    # 'show interface brief' finds static LAGs absent from the REST Port/Interface
    # tables (v1 API firmware).  'show lacp interfaces' adds LACP member mappings.
    # 'show interface <lag>' is a last-resort member lookup for static LAGs.
    if hasattr(self, 'device'):
        try:
            brief = self.device.send_command('show interface brief')
            _log.debug('show interface brief output:\n%s', brief)
            for line in brief.splitlines():
                m = re.match(r'^\s*(lag\d+)\s', line, re.IGNORECASE)
                if m:
                    lag = m.group(1).lower()
                    if lag not in interfaces_return:
                        interfaces_return[lag] = {
                            'is_up': bool(re.search(r'\bup\b', line, re.IGNORECASE)),
                            'is_enabled': True,
                            'description': '',
                            'last_flapped': -1.0,
                            'speed': 0,
                            'mtu': 0,
                            'mac_address': '',
                            'children': [],
                            've_children': [],
                            'type': None,
                        }
        except Exception as e:
            _log.debug('show interface brief failed: %s', e)

        try:
            lacp_out = self.device.send_command('show lacp interfaces')
            _log.debug('show lacp interfaces output:\n%s', lacp_out)
            for line in lacp_out.splitlines():
                m = re.match(r'^(\d+/\d+/\d+)\s+(lag\d+)', line.strip())
                if m:
                    port, lag = m.group(1), m.group(2)
                    if lag not in interfaces_return:
                        interfaces_return[lag] = {
                            'is_up': False,
                            'is_enabled': True,
                            'description': '',
                            'last_flapped': -1.0,
                            'speed': 0,
                            'mtu': 0,
                            'mac_address': '',
                            'children': [],
                            've_children': [],
                            'type': None,
                        }
                    interfaces_return[lag]['children'].append(port)
        except Exception as e:
            _log.debug('show lacp interfaces failed: %s', e)

        # For any LAG still lacking children, try 'show interface <lag>'
        for lag in [n for n in interfaces_return if re.match(r'^lag\d+$', n, re.IGNORECASE)]:
            if interfaces_return[lag]['children']:
                continue
            try:
                out = self.device.send_command(f'show interface {lag}')
                for line in out.splitlines():
                    # "  Aggregated-interfaces : 1/1/47 1/1/48" (space-separated on one line)
                    if re.search(r'[Aa]ggregat|[Mm]ember', line):
                        for port in re.findall(r'\d+/\d+/\d+', line):
                            interfaces_return[lag]['children'].append(port)
                    # or each member on its own indented line
                    else:
                        m = re.match(r'^\s+(\d+/\d+/\d+)\s*$', line)
                        if m:
                            interfaces_return[lag]['children'].append(m.group(1))
            except Exception:
                pass

        # Deduplicate and sort all LAG children lists
        for lag in list(interfaces_return):
            if re.match(r'^lag\d+$', lag, re.IGNORECASE) and interfaces_return[lag]['children']:
                interfaces_return[lag]['children'] = sorted(set(interfaces_return[lag]['children']))

    # Normalise LAG interface names: 'lag10' → 'lag 10' (Nautobot stores with space)
    for old in list(interfaces_return):
        if re.match(r'^lag\d+$', old, re.IGNORECASE):
            new = re.sub(r'^(lag)(\d+)$', r'\1 \2', old, flags=re.IGNORECASE)
            if old != new:
                interfaces_return[new] = interfaces_return.pop(old)

    return interfaces_return


# Saved before patching — prevents infinite recursion when aoscx_get_interfaces_ip
# calls the original; if we called AOSCXDriver.get_interfaces_ip(self) after patching
# it would call ourselves again.
_orig_get_interfaces_ip = None


def aoscx_get_interfaces_ip(self):
    """
    Replacement for AOSCXDriver.get_interfaces_ip that normalises VLAN
    interface names to match what aoscx_get_interfaces produces.

    The stock driver returns 'vlan707'; Nautobot stores 'vlan 707'.  Without
    normalisation the management-IP lookup in the nornir job misses the VLAN
    interface and the IP / management flag are never written.
    """
    raw = _interfaces_ip_from_bulk(_bulk_interfaces(self))
    result = {}
    for name, data in raw.items():
        normalised = re.sub(r'^(vlan)(\d+)$', r'\1 \2', name, flags=re.IGNORECASE)
        result[normalised] = data
    return result


def aoscx_get_facts(self):
    """
    AOSCXDriver.get_facts, reusing the bulk interfaces payload for interface_list.

    The stock version makes an extra un-timed GET system/interfaces just for the
    names; on loaded CX6100s that single call alone exceeded 30s.
    """
    from pyaoscx import system
    systeminfo = system.get_system_info(**self.session_info)
    productinfo = system.get_product_info(**self.session_info)
    return {
        'uptime': int(systeminfo['boot_time']) / 1000,
        'vendor': 'Aruba',
        'os_version': systeminfo['software_info']['build_id'],
        'serial_number': productinfo['product_info']['serial_number'],
        'model': productinfo['product_info']['product_name'],
        'hostname': systeminfo['hostname'],
        'fqdn': systeminfo['hostname'],
        'interface_list': list(_bulk_interfaces(self)),
    }


def _split_prefix(address):
    return address[:address.rfind('/')], int(address[address.rfind('/') + 1:])


def _interfaces_ip_from_bulk(bulk):
    """
    AOSCXDriver.get_interfaces_ip output, computed from the bulk payload.

    Unlike the stock driver, IPv4 and each IPv6 key are parsed independently.
    On some firmware 'ip6_addresses' is a URI string pointing at a
    sub-collection (no IPv6 configured) rather than a dict of addresses; the
    stock driver iterated that string character by character, raised, and its
    catch-all skipped the whole interface, IPv4 included, so management VLAN
    IPs went missing.
    """
    result = {}
    for name, info in bulk.items():
        info = info or {}
        entry = {}
        ip4 = info.get('ip4_address')
        if isinstance(ip4, str) and '/' in ip4:
            try:
                addr, plen = _split_prefix(ip4)
                entry['ipv4'] = {addr: {'prefix_length': plen}}
            except ValueError:
                _log.warning('AOS-CX: could not parse ip4_address %r on %s', ip4, name)
        ipv6 = {}
        for key in ('ip6_addresses', 'ip6_address_link_local', 'ip6_autoconfigured_addresses'):
            value = info.get(key)
            if not isinstance(value, (dict, list)):
                continue  # absent, or a URI reference to an empty sub-collection
            for address in value:
                try:
                    addr, plen = _split_prefix(address)
                    ipv6[addr] = {'prefix_length': plen}
                except (ValueError, TypeError):
                    _log.warning('AOS-CX: could not parse %s %r on %s', key, address, name)
        if ipv6:
            entry['ipv6'] = ipv6
        if entry:
            result[name] = entry
    return result


# Apply patches at import time.  Save originals BEFORE patching so our
# wrappers can call them without recursing into themselves.
_orig_open = None
try:
    from napalm_aoscx.aoscx import AOSCXDriver as _AOSCXDriver
    _orig_get_interfaces_ip = _AOSCXDriver.get_interfaces_ip
    # Guard against double-patching (module reload) — wrapping our own wrapper recurses.
    _orig_open = getattr(_AOSCXDriver.open, "_sohonet_orig", _AOSCXDriver.open)
    aoscx_open._sohonet_orig = _orig_open
    _AOSCXDriver.open = aoscx_open
    _AOSCXDriver.close = aoscx_close
    _AOSCXDriver.get_interfaces_ip = aoscx_get_interfaces_ip
    _AOSCXDriver.get_facts = aoscx_get_facts
except ImportError:
    pass

# Bounded, exception-raising REST login/logout for every pyaoscx user in this
# process (the driver calls session.login/logout via the module attribute).
try:
    from pyaoscx import session as _pyaoscx_session
    _pyaoscx_session.login = aoscx_rest_login
    _pyaoscx_session.logout = aoscx_rest_logout
except ImportError:
    pass

# pyaoscx.port._get_port_v1 hard-codes timeout=2, which is too short for
# slower devices or higher-latency VPN paths. Replace it with timeout=30.
try:
    import logging as _logging
    from pyaoscx import common_ops as _pyaoscx_common_ops
    import pyaoscx.port as _pyaoscx_port

    def _get_port_v1_patched(port_name, depth=0, selector=None, **kwargs):
        if selector not in ['configuration', 'status', 'statistics', None]:
            raise Exception("ERROR: Selector should be 'configuration', 'status', or 'statistics'")
        payload = {"depth": depth, "selector": selector}
        port_name_percents = _pyaoscx_common_ops._replace_special_characters(port_name)
        target_url = kwargs["url"] + "system/ports/%s" % port_name_percents
        response = kwargs["s"].get(target_url, verify=False, params=payload, timeout=30)
        port_name = _pyaoscx_common_ops._replace_percents(port_name_percents)
        if not _pyaoscx_common_ops._response_ok(response, "GET"):
            _logging.warning("FAIL: Getting Port table entry '%s' failed with status code %d: %s"
                             % (port_name, response.status_code, response.text))
            return {}
        _logging.info("SUCCESS: Getting Port table entry '%s' succeeded" % port_name)
        return response.json()

    _pyaoscx_port._get_port_v1 = _get_port_v1_patched
except (ImportError, AttributeError):
    pass
