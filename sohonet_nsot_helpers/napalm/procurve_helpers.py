import re
import os

import textfsm
from netaddr import IPAddress
from nornir_napalm.plugins.tasks import napalm_get, napalm_cli

from napalm_procurve.procurve import ProcurveDriver


def procurve_get_interfaces(self):
    """Parse brief interface overview"""
    interfaces = {}
    ifs = self._get_interface_map()

    # Initialize custom attributes
    if not hasattr(self, 'vlans'):
        show_vlans_output = self._send_command("show vlans")
        self.vlans = _textfsm_extractor("procurve_show_vlans", show_vlans_output)

    if_alias = self._walkMIB_values("ifAlias")
    if_macs = self._walkMIB_values("ifPhysAddress")
    if_mtu = self._walkMIB_values("ifMtu")
    if_adm_state = self._walkMIB_values("ifAdminStatus")
    if_lnk_state = self._walkMIB_values("ifOperStatus")

    for ifn, idx in ifs.items():
        interfaces[str(ifn)] = {
            "is_up": True if if_lnk_state[idx] == "1" else False,
            "is_enabled": True if if_adm_state[idx] == "1" else False,
            "description": str(if_alias[idx]),
            "last_flapped": -1.0,
            "speed": 0,
            "mac_address": str(if_macs[idx]),
            "mtu": int(re.sub(",", "", if_mtu[idx])),
        }

    # Add speeds & interface type
    show_interface_custom_output = self._send_command("show interfaces custom all port:10 type")
    data = _textfsm_extractor("procurve_show_interfaces_custom", show_interface_custom_output)
    if not data:
        show_interface_config_putput = self._send_command("show interfaces config")
        data = _textfsm_extractor("procurve_show_interfaces_custom", show_interface_config_putput)
    for row in data:
        # Determine interface speed
        speed = 1000
        if row['type'] in ['SFP+SR', 'SFP+LR', 'SFP+DA1', '10GbE-SR', '10GbE-CX4', '10GbE-T']:
            speed = 10000

        intf_type = '1000base-t'
        if row['type'] in ['SFP+SR', 'SFP+LR', 'SFP+DA1', '10GbE-SR']:
            intf_type = '10gbase-x-sfpp'
        elif row['type'] == '10GbE-CX4':
            intf_type = '10gbase-cx4'
        elif row['type'] == '10GbE-T':
            intf_type = '10gbase-t'

        # Strip -TrkX strings from port names
        portname = re.sub(r'-.*', '', row['port'])

        interfaces[portname]['speed'] = speed
        interfaces[portname]['type'] = intf_type

    # Append trunk children
    trunks = _procurve_get_trunks(self)
    for trunk, data in trunks.items():
        interfaces[trunk]['children'] = data['interfaces']

    # Set type virtual for VLAN interfaces
    if not hasattr(self, 'vlans'):
        show_vlans_output = self._send_command("show vlans")
        self.vlans = _textfsm_extractor("procurve_show_vlans", show_vlans_output)
    for vlan in self.vlans:
        vlan_interface = _vid_to_interface(self, vlan['vlan'])
        interfaces[vlan_interface]['type'] = 'virtual'

    return interfaces


def procurve_get_interfaces_ip(self):
    ''' napalm get_interfaces_ip function '''
    ifs = self._get_interface_map()
    if_alias = self._walkMIB_values("ifAlias")

    ips = {}

    # Get show ip output, and process valid lines including ip address
    show_ip_output = self._send_command("show ip")
    # Run alternate command to get ip info if any fields are truncated. Default to show ip to support
    # older models
    if '...' in show_ip_output:
        show_ip_output = self._send_command("show vlan custom id name ipconfig ipaddr ipmask")
    show_ip = _textfsm_extractor("procurve_show_ip", show_ip_output)
    for ip in show_ip:
        vlan_name = ip['vlan']
        if vlan_name not in ifs.keys():
            # Lookup interface index from alias
            idx = list(k for k, v in if_alias.items() if v == vlan_name)[0]
            # Lookup interface name from idx
            vlan_name = list(k for k, v in ifs.items() if v == idx)[0]

        ips.update(
            {vlan_name: {
                "ipv4": {
                    ip['ipaddress']: {
                        "prefix_length": IPAddress(ip['subnetmask']).netmask_bits()
                    }
                }
            }})

    return ips


def procurve_get_vlans(self):

    result = {}

    # Get list of vlans, populate name to result dict
    if not hasattr(self, 'vlans'):
        show_vlans_output = self._send_command("show vlans")
        self.vlans = _textfsm_extractor("procurve_show_vlans", show_vlans_output)
    for vlan in self.vlans:
        vlan_interface = _vid_to_interface(self, vlan['vlan'])
        result[vlan['vlan']] = {'name': vlan['name'], 'interfaces': [vlan_interface]}

    # Get VLANs for interfaces
    show_interfaces_status_output = self._send_command("show interfaces status")
    interfaces = _textfsm_extractor("procurve_show_interfaces_status", show_interfaces_status_output)
    for interface in interfaces:
        # Exclude trunk ports
        if 'Trk' in interface['port']:
            continue

        if interface['untaggedvlan'].isdigit():
            result[interface['untaggedvlan']]['interfaces'].append(interface['port'])

        if interface['taggedvlans'].isdigit():
            result[interface['taggedvlans']]['interfaces'].append(interface['port'])

        if interface['taggedvlans'] == 'multi':
            intf_show_vlans_output = self._send_command(f"show vlans ports {interface['port']}")
            intf_vlans = _textfsm_extractor("procurve_show_vlans", intf_show_vlans_output)
            for vlan in intf_vlans:
                result[vlan['vlan']]['interfaces'].append(interface['port'])

    # Get VLANs for trunks
    trunks = _procurve_get_trunks(self)
    for trunk in trunks.keys():
        trunk_show_vlans_output = self._send_command(f"show vlans ports {trunk}")
        trunk_vlans = _textfsm_extractor("procurve_show_vlans", trunk_show_vlans_output)
        for vlan in trunk_vlans:
            result[vlan['vlan']]['interfaces'].append(trunk)

    return result


def procurve_get_interfaces_vlans(self):
    """return dict as documented at
        https://github.com/napalm-automation/napalm/issues/919#issuecomment-485905491"""

    result = {}

    # Collect data for standard interfaces
    show_interfaces_status_output = self._send_command("show interfaces status")
    interfaces = _textfsm_extractor("procurve_show_interfaces_status", show_interfaces_status_output)
    for interface in interfaces:
        # Strip -TrkX strings from port names
        portname = re.sub(r'-.*', '', interface['port'])

        # Untagged interfaces
        if interface['taggedvlans'] == 'No':
            result[portname] = {
                'mode': 'access',
                'access-vlan': interface['untaggedvlan'],
                'trunk_vlans': [],
                'native_vlan': -1,
                'tagged-native-vlan': False,
            }

        # Tagged interfaces
        else:
            result[portname] = {
                'mode': 'trunk',
                'access-vlan': -1,
                'trunk-vlans': [interface['taggedvlans']],
                'native-vlan': -1 if interface['untaggedvlan'] == 'No' else interface['untaggedvlan'],
                'tagged-native-vlan': False,
            }
            if interface['taggedvlans'] == 'multi':
                result[portname]['trunk-vlans'] = []
                intf_show_vlans_output = self._send_command(f"show vlans ports {interface['port']}")
                intf_vlans = _textfsm_extractor("procurve_show_vlans", intf_show_vlans_output)
                for vlan in intf_vlans:
                    result[portname]['trunk-vlans'].append(vlan['vlan'])

    # Collect data for trunks
    trunks = _procurve_get_trunks(self)
    for trunk in trunks.keys():
        trunk_show_vlans_output = self._send_command(f"show vlans ports {trunk}")
        trunk_vlans = _textfsm_extractor("procurve_show_vlans", trunk_show_vlans_output)

        result[trunk] = {
            'mode': 'trunk',
            'access-vlan': -1,
            'trunk-vlans': [vlan['vlan'] for vlan in trunk_vlans],
            'native-vlan': -1,
            'tagged-native-vlan': True
        }

    # Collect data for VLAN interfaces
    if not hasattr(self, 'vlans'):
        show_vlans_output = self._send_command("show vlans")
        self.vlans = _textfsm_extractor("procurve_show_vlans", show_vlans_output)
    for vlan in self.vlans:
        vlan_interface = _vid_to_interface(self, vlan['vlan'])

        result[vlan_interface] = {
            'mode': 'access',
            'access-vlan': vlan['vlan'],
            'trunk_vlans': [],
            'native_vlan': -1,
            'tagged-native-vlan': False,
        }

    return result


def _procurve_get_trunks(self):
    ''' return a dict with key of trunk name, and value a dict containing name and list of interfaces for the trunk
  i.e.
  { 'trk1': {
    'name': 'uplink',
    'interfaces': [ 45, 46, 47, 48 ]
  }
  '''
    result = {}
    show_trunks_output = self._send_command("show trunks")
    trunks = _textfsm_extractor("procurve_show_trunks", show_trunks_output)
    for port in trunks:
        trunk_name = port['group']
        if trunk_name not in result.keys():
            result[trunk_name] = {'name': port['name'], 'interfaces': []}
        result[trunk_name]['interfaces'].append(port['port'])

    return result


def _textfsm_extractor(template, raw_text):
    ''' Apply textfsm templates on raw_text'''
    textfsm_data = list()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = f"{current_dir}/../textfsm_templates/{template}.tpl"

    with open(template_path) as f:
        fsm_handler = textfsm.TextFSM(f)
        for obj in fsm_handler.ParseText(raw_text):
            entry = {}
            for index, entry_value in enumerate(obj):
                entry[fsm_handler.header[index].lower()] = entry_value
            textfsm_data.append(entry)

        return textfsm_data


def _vid_to_interface(self, vid):
    ''' VLAN interfaces names from dot1qVlanStaticName or VLANXXXX convention'''
    ifs = self._get_interface_map()
    if not hasattr(self, 'vlan_map'):
        self.vlan_map = self._walkMIB_values("dot1qVlanStaticName")

    # VLAN Interface names are sometimes the description of the VLAN, and sometimes just VLANXXXX
    # Try to determine the correct interface name
    vlan_name = self.vlan_map[str(vid)]
    if vlan_name in ifs.keys():
        return vlan_name

    if int(vid) == 1:
        return 'DEFAULT_VLAN'

    return f"VLAN{vid}"
