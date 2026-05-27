# sohonet_nsot_helpers/napalm/arubacx_helpers.py

import logging
import re
from urllib.parse import unquote

_log = logging.getLogger(__name__)


def aoscx_close(self):
    """
    Safe replacement for AOSCXDriver.close().

    The stock close() calls session.logout(**self.session_info) then
    self.device.disconnect(). If open() failed before the REST login
    completed, session_info is {} and logout(**{}) raises KeyError,
    killing the billiard worker process. Guard both calls.
    """
    if self.session_info.get('s') and self.session_info.get('url'):
        try:
            from pyaoscx import session
            session.logout(**self.session_info)
        except Exception:
            pass
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
    interface_list = pyaoscx_interface.get_all_interface_names(**self.session_info)

    for name in interface_list:
        iface = pyaoscx_interface.get_interface(name, **self.session_info)
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
            _log.warning('DEBUG hw_intf_info for %s (speed=0): %s', name, hw_info)

        interfaces_return[name] = {
            'is_up': iface.get('link_state') == 'up',
            'is_enabled': iface.get('admin_state') == 'up',
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
            iface = pyaoscx_interface.get_interface('mgmt', **self.session_info)
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
                _log.warning('DEBUG show interface mgmt output:\n%s', out)
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
                _log.warning('DEBUG show interface mgmt failed: %s', e)


    # Normalise VLAN interface names: 'vlan707' → 'vlan 707'
    for old in list(interfaces_return):
        if re.match(r'^vlan\d+$', old, re.IGNORECASE):
            new = re.sub(r'^(vlan)(\d+)$', r'\1 \2', old, flags=re.IGNORECASE)
            if old != new:
                interfaces_return[new] = interfaces_return.pop(old)

    # --- REST API: depth=1 for LAG member discovery (works on v10.04+ firmware
    # where LAGs are in the Interface table, not the Port table).
    lag_names = [n for n in interfaces_return if re.match(r'^lag\d+$', n, re.IGNORECASE)]
    for lag_name in lag_names:
        try:
            lag_data = pyaoscx_interface.get_interface(lag_name, depth=1, **self.session_info)
            interfaces_field = lag_data.get('interfaces', {})
            children = []
            for uri in (interfaces_field if isinstance(interfaces_field, list)
                        else interfaces_field.keys()):
                port_name = unquote(str(uri).rstrip('/').split('/')[-1])
                if re.match(r'^\d+/\d+/\d+', port_name):
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
            _log.warning('DEBUG show interface brief output:\n%s', brief)
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
            _log.warning('DEBUG show interface brief failed: %s', e)

        try:
            lacp_out = self.device.send_command('show lacp interfaces')
            _log.warning('DEBUG show lacp interfaces output:\n%s', lacp_out)
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
            _log.warning('DEBUG show lacp interfaces failed: %s', e)

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
    raw = _orig_get_interfaces_ip(self)
    result = {}
    for name, data in raw.items():
        normalised = re.sub(r'^(vlan)(\d+)$', r'\1 \2', name, flags=re.IGNORECASE)
        result[normalised] = data
    return result


# Apply patches at import time.  Save originals BEFORE patching so our
# wrappers can call them without recursing into themselves.
try:
    from napalm_aoscx.aoscx import AOSCXDriver as _AOSCXDriver
    _orig_get_interfaces_ip = _AOSCXDriver.get_interfaces_ip
    _AOSCXDriver.close = aoscx_close
    _AOSCXDriver.get_interfaces_ip = aoscx_get_interfaces_ip
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
