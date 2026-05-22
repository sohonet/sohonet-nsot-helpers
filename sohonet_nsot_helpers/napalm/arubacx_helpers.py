# sohonet_nsot_helpers/napalm/arubacx_helpers.py

import logging
import re
from urllib.parse import unquote

_log = logging.getLogger(__name__)


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
            speed = int(hw_info.get('max_speed') or 0) if isinstance(hw_info, dict) else 0
        except (ValueError, TypeError):
            speed = 0
        try:
            mtu = int(iface.get('mtu') or 0)
        except (ValueError, TypeError):
            mtu = 0
        mac = (hw_info.get('mac_addr') or '') if isinstance(hw_info, dict) else ''

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

    # Explicitly fetch OOB management port — omitted by get_all_interface_names().
    if 'mgmt' not in interfaces_return:
        try:
            iface = pyaoscx_interface.get_interface('mgmt', **self.session_info)
            description = iface.get('description', '') or ''
            hw_info = iface.get('hw_intf_info', {}) or {}
            try:
                speed = int(hw_info.get('max_speed') or 0) if isinstance(hw_info, dict) else 0
            except (ValueError, TypeError):
                speed = 0
            try:
                mtu = int(iface.get('mtu') or 0)
            except (ValueError, TypeError):
                mtu = 0
            mac = (hw_info.get('mac_addr') or '') if isinstance(hw_info, dict) else ''
            interfaces_return['mgmt'] = {
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
        except Exception:
            pass

    # Normalise VLAN interface names: 'vlan707' → 'vlan 707'
    vlan_renames = {
        name: re.sub(r'^(vlan)(\d+)$', r'\1 \2', name, flags=re.IGNORECASE)
        for name in list(interfaces_return)
        if re.match(r'^vlan\d+$', name, re.IGNORECASE)
    }
    for old, new in vlan_renames.items():
        if old != new:
            interfaces_return[new] = interfaces_return.pop(old)

    lag_names = [n for n in interfaces_return if re.match(r'^lag\d+$', n, re.IGNORECASE)]

    # --- REST API: depth=1 expands the 'interfaces' reference so we can
    # read member port names from the dict keys (each key is a full URI
    # like "/rest/v1/system/interfaces/1%2F1%2F47").
    for lag_name in lag_names:
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

    # --- CLI fallback: always run to catch LAGs absent from the Interface
    # table (v1 API firmware stores them in the Port table instead).
    # Creates a new entry in interfaces_return when a LAG is found in CLI
    # output but was never returned by get_all_interface_names().
    if hasattr(self, 'device'):
        try:
            output = self.device.send_command('show lacp interfaces')
            for line in output.splitlines():
                m = re.match(r'^(\d+/\d+/\d+)\s+\w+\s+(lag\d+)', line.strip())
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
            for lag in list(interfaces_return):
                if re.match(r'^lag\d+$', lag, re.IGNORECASE) and interfaces_return[lag]['children']:
                    interfaces_return[lag]['children'] = sorted(set(interfaces_return[lag]['children']))
        except Exception:
            pass

    return interfaces_return
