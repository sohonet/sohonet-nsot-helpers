# sohonet_nsot_helpers/napalm/arubacx_helpers.py

import re
from urllib.parse import unquote


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
      2. CLI fallback via 'show lacp interfaces' — for firmware versions
         where LAGs are in the Port table (v1 API) and the REST approach
         returns no members. Only covers LACP LAGs, but static LAGs are
         very rare in this environment.
    """
    from pyaoscx import interface as pyaoscx_interface

    interfaces_return = {}
    interface_list = pyaoscx_interface.get_all_interface_names(**self.session_info)

    for name in interface_list:
        iface = pyaoscx_interface.get_interface(name, **self.session_info)
        description = iface.get('description', '') or ''
        hw_info = iface.get('hw_intf_info', {}) or {}
        speed = hw_info.get('max_speed', 0) if isinstance(hw_info, dict) else 0
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

    # --- CLI fallback: for any LAG that REST returned no members for,
    # parse 'show lacp interfaces'. Requires the open netmiko session
    # (self.device), which is always present since use_cli=True is forced.
    lags_needing_cli = [n for n in lag_names if not interfaces_return[n]['children']]
    if lags_needing_cli and hasattr(self, 'device'):
        try:
            output = self.device.send_command('show lacp interfaces')
            for line in output.splitlines():
                m = re.match(r'^(\d+/\d+/\d+)\s+\w+\s+(lag\d+)', line.strip())
                if m:
                    port, lag = m.group(1), m.group(2)
                    if lag in interfaces_return:
                        interfaces_return[lag]['children'].append(port)
            for lag in lag_names:
                if interfaces_return[lag]['children']:
                    interfaces_return[lag]['children'] = sorted(interfaces_return[lag]['children'])
        except Exception:
            pass

    return interfaces_return
