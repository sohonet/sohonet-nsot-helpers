from nornir_napalm.plugins.tasks import napalm_get, napalm_cli
import ipaddress
import json
import re
import textfsm
import os

import pyeapi
import napalm.base.helpers

from napalm.eos.eos import EOSDriver
from pyeapi.eapilib import CommandError

HOST_PREFIX_LENGTH = 32


def transform_arista_vlans(vlan_dict):
    ''' Generate a NAPALM compabile vlan dict '''
    vlans = {}
    for vid, value in vlan_dict['vlans'].items():
        interfaces = list(value['interfaces'].keys())
        # Arista reports 'Cpu' in ports list if there is a virtual interface for the vlan. Change this to VlanXX
        if 'Cpu' in interfaces:
            interfaces.remove('Cpu')
            interfaces.append('Vlan{}'.format(vid))
        # Remove PeerEthernet and PeerPort interfaces
        interfaces = [intf for intf in interfaces if 'Peer' not in intf]
        vlans[vid] = {
            'name': value['name'],
            'interfaces': [i for i in interfaces if 'Peer' not in i],  # Remove PeerEthernet interfaces
        }

    return vlans


def get_subinterface_vlan(device, interface):
    # Construct command to run
    intftype = re.sub(r'(\D+)([\d|\.|/]+)', r'\1', interface)
    intfnum = re.sub(r'(\D+)([\d|\.|/]+)', r'\2', interface)
    output = device.run_commands(['show running-config interfaces {} {}'.format(intftype, intfnum)], encoding='text')

    # Extract VLAN ID from output
    if 'encapsulation dot1q vlan' in output[0]['output']:
        vlan_regex = re.compile(r'.*encapsulation dot1q vlan (?:\d+ inner )?(\d+).*', re.DOTALL)
        vlan = re.sub(vlan_regex, r'\1', output[0]['output'])
        return vlan

    return False


def get_patch_panel_vlans(device):
    ''' return a dict of vlans and interfaces from patch panels

    {
        1: {
            'interfaces' : [ 'Ethernet1', 'Ethernet2' ]
        }
    }
    '''
    output = device.run_commands(['show running-config section patch'], encoding='text')
    matches = re.findall(r'interface (\S+) dot1q vlan (\d+)', output[0]['output'])

    vlans = {}
    for match in matches:
        vid = match[1]
        if vid in vlans:
            vlans[vid]['interfaces'].append(match[0])
        else:
            vlans[vid] = {'interfaces': [match[0]]}

    return vlans


def get_eos_vlans(task):
    ''' Helper task that can be used with nornir '''
    r = task.run(task=napalm_cli, commands=['show vlan|json'])
    vlan_dict = json.loads(r.result['show vlan|json'])['vlans']
    return vlan_dict


def eos_get_vlans(self):
    ''' Monkeypatch for get_vlans to use with EOSDriver in napalm '''
    commands = ['show vlan', 'show interfaces']
    output = self.device.run_commands(commands, encoding='json')

    # Process show vlans output
    vlans = transform_arista_vlans(output[0])

    # also get shutdown virtual interfaces
    for interface in [i for i in output[1]['interfaces'].keys() if 'Vlan' in i]:
        vlan = interface[4:]
        if vlan in vlans.keys() and interface not in vlans[vlan]['interfaces']:
            vlans[vlan]['interfaces'].append(interface)

    # Get vlans from subinterfaces
    for interface in [i for i in output[1]['interfaces'].keys() if '.' in i]:
        vlan = get_subinterface_vlan(self.device, interface)

        if vlan:
            # Update vlans dict
            if vlan in vlans.keys():
                vlans[vlan]['interfaces'].append(interface)
            else:
                vlans[vlan] = {
                    'interfaces': [interface],
                    'name': output[1]['interfaces'][interface]['description'],
                }

    # Get vlans from patch panels
    pp_vlans = get_patch_panel_vlans(self.device)
    for vlan, data in pp_vlans.items():
        if vlan in vlans.keys():
            vlans[vlan]['interfaces'] += data['interfaces']
        else:
            vlans[vlan] = {
                'interfaces': data['interfaces'],
                'name': '',
            }

    return vlans


def eos_get_interfaces_vlans(self):
    ''' Implement get_interfaces_vlans '''
    commands = ['show interfaces', 'show interfaces trunk', 'show vlan']
    output = self.device.run_commands(commands, encoding='json')

    result = {}

    # Generate list of interfaces
    for interface in output[0]['interfaces'].keys():
        # Ignore PeerEthernet interfaces
        if 'Peer' in interface:
            continue
        result[interface] = {
            'mode': 'access',
            'access-vlan': -1,
            'trunk-vlans': [],
            'native-vlan': -1,
            'tagged-native-vlan': False
        }

    # Trunks will report tagged vlans
    for trunk, data in output[1]['trunks'].items():
        # Populate missing keys with empty dataset
        if 'activeVlans' not in data.keys():
            data['activeVlans'] = {'vlanIds': []}
        nativevlan = -1
        if data['nativeVlan'] in data['allowedVlans']['vlanIds']:
            nativevlan = data['nativeVlan']
        result[trunk] = {
            'mode': 'trunk',
            'trunk-vlans': data['activeVlans']['vlanIds'],
            'native-vlan': nativevlan,
            'tagged-native-vlan': True
        }

    # Any other vlans in show vlans will be access vlans
    for vlan, data in output[2]['vlans'].items():
        for interface in data['interfaces'].keys():
            # Arista reports 'Cpu' in ports list if there is a virtual interface for the vlan. Change this to VlanXX
            if interface == 'Cpu':
                interface = 'Vlan{}'.format(vlan)

            # Ignore PeerEthernet interfaces
            if 'Peer' in interface:
                continue

            if result[interface]['mode'] == 'access':
                result[interface]['access-vlan'] = vlan
    
    # Disabled arista virtual interfaces will be skipped above, add the access vlan for those
    for interface in [i for i in output[0]['interfaces'].keys() if 'Vlan' in i]:
        vlan = interface[4:]
        if result[interface]['access-vlan'] != vlan:
            result[interface]['access-vlan'] = vlan

    # Add vlans for subinterfaces
    for interface in [i for i in output[0]['interfaces'].keys() if '.' in i]:
        vlan = get_subinterface_vlan(self.device, interface)
        if vlan:
            result[interface]['access-vlan'] = vlan

    # Add vlans from patch panels
    pp_vlans = get_patch_panel_vlans(self.device)
    for vlan, data in pp_vlans.items():
        for interface in data['interfaces']:
            result[interface]['trunk-vlans'].append(vlan)
            result[interface]['mode'] = 'trunk'

    return result


def eos_get_interfaces(self):
    ''' Monkeypatch to add port-channel children in get_interfaces '''
    commands = ['show interfaces', 'show interfaces status']
    cmd_result = self.device.run_commands(commands)

    try:
        mpls_result = self.device.run_commands(['show mpls interface'])
        show_mpls_interface = mpls_result[0]
    except CommandError as e:
        show_mpls_interface = {'intfs': {}}

    show_interfaces = cmd_result[0]
    show_interfaces_status = cmd_result[1]

    interfaces = {}

    for interface, values in show_interfaces['interfaces'].items():
        interfaces[interface] = {}

        if values['lineProtocolStatus'] == 'up':
            interfaces[interface]['is_up'] = True
            interfaces[interface]['is_enabled'] = True
        else:
            interfaces[interface]['is_up'] = False
            if values['interfaceStatus'] == 'disabled':
                interfaces[interface]['is_enabled'] = False
            else:
                interfaces[interface]['is_enabled'] = True

        interfaces[interface]['description'] = values['description']

        interfaces[interface]['last_flapped'] = values.pop('lastStatusChangeTimestamp', -1.0)

        interfaces[interface]["mtu"] = int(values["mtu"])
        interfaces[interface]['speed'] = int(values['bandwidth'] * 1e-6)
        interfaces[interface]['mac_address'] = values.pop('physicalAddress', '')

        if 'memberInterfaces' in values:
            interfaces[interface]['children'] = [i for i in values['memberInterfaces'].keys() if 'Peer' not in i]

        if interface in show_mpls_interface['intfs'].keys():
            interfaces[interface]["mpls_enabled"] = show_mpls_interface['intfs'][interface]['ldpConfigured']
        else:
            interfaces[interface]["mpls_enabled"] = False

    for interface, values in show_interfaces_status['interfaceStatuses'].items():
        if values['interfaceType'] == '10GBASE-T':
            interfaces[interface]['type'] = '10gbase-t'
        elif values['interfaceType'] == '10GBASE-SRL':
            interfaces[interface]['type'] = '10gbase-x-sfpp'
        elif values['interfaceType'] == '25GBASE-CR':
            interfaces[interface]['type'] = '25gbase-x-sfp28'
        elif values['interfaceType'] in ['40GBASE-SR4', '40GBASE-LR4']:
            interfaces[interface]['type'] = '40gbase-x-qsfpp'
        elif values['interfaceType'] in ['100GBASE-CR4', '100GBASE-SR4']:
            interfaces[interface]['type'] = '100gbase-x-qsfp28'

    return interfaces


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


def _virtual_ip_prefix_length(ipv4_addresses, address):
    ''' Prefix length the interface subnet containing address gives a maskless VARP address

    EOS omits the mask on 'ip virtual-router address', but a VARP address is not a host
    route -- it inherits the mask of the interface subnet it sits in. Assuming /32 attaches
    is_virtual to a host prefix that the importer then drops as covered by its parent, so
    the subnet the address really belongs to is never flagged as virtual.

    ipv4_addresses is the interface's {address: {prefix_length, is_virtual}} mapping, so
    only call this once the interface's real addresses have been recorded. A masked
    'ip virtual-router address' defines a virtual subnet of its own, so virtual entries are
    valid donors too -- only host entries are skipped, so an address already recorded as a
    /32 cannot answer for itself.

    Falls back to /32 when no subnet covers the address, which is the most that can be said
    for a virtual address on an interface with no matching subnet.
    '''
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return HOST_PREFIX_LENGTH

    for existing, details in ipv4_addresses.items():
        try:
            network = ipaddress.ip_interface(f"{existing}/{details['prefix_length']}").network
        except ValueError:
            continue
        if network.prefixlen < network.max_prefixlen and addr in network:
            return network.prefixlen

    return HOST_PREFIX_LENGTH


def eos_get_interfaces_ip(self):
    """Updated to also include the VRF name and Interface ACL"""

    interfaces_ip = {}

    interfaces_ipv4_out = self.device.run_commands(["show ip interface"])[0]["interfaces"]
    try:
        interfaces_ipv6_out = self.device.run_commands(["show ipv6 interface"])[0]["interfaces"]
    except pyeapi.eapilib.CommandError as e:
        msg = str(e)
        if "No IPv6 configured interfaces" in msg:
            interfaces_ipv6_out = {}
        else:
            raise

    interface_config = self.device.run_commands(["show running-config | section interface"],
                                                encoding="text")[0]["output"]
    interface_acls = _textfsm_extractor("eos_show_running_config_interface_acl", interface_config)
    interface_virtual_ips = _textfsm_extractor("eos_show_running_config_interface_virtual_router", interface_config)

    for interface_name, interface_details in interfaces_ipv4_out.items():
        
        ipv4_list = []
        if interface_name not in interfaces_ip.keys():
            interfaces_ip[interface_name] = {}

        if "ipv4" not in interfaces_ip.get(interface_name):
            interfaces_ip[interface_name]["ipv4"] = {}
        if "ipv6" not in interfaces_ip.get(interface_name):
            interfaces_ip[interface_name]["ipv6"] = {}

        iface_details = interface_details.get("interfaceAddress", {})
        if iface_details.get("primaryIp", {}).get("address") != "0.0.0.0":
            ipv4_list.append({
                "address": napalm.base.helpers.ip(iface_details.get("primaryIp", {}).get("address")),
                "masklen": iface_details.get("primaryIp", {}).get("maskLen"),
            })
        for secondary_ip in iface_details.get("secondaryIpsOrderedList", []):
            ipv4_list.append({
                "address": napalm.base.helpers.ip(secondary_ip.get("address")),
                "masklen": secondary_ip.get("maskLen"),
            })
        
        for ip in ipv4_list:
            if not ip.get("address"):
                continue
            if ip.get("address") not in interfaces_ip.get(interface_name).get("ipv4"):
                interfaces_ip[interface_name]["ipv4"][ip.get("address")] = {"prefix_length": ip.get("masklen"), "is_virtual": False}

        interfaces_ip[interface_name]["vrf"] = interface_details.get('vrf')
    
    for i in interface_virtual_ips:
        if not i["ipaddress"] or i["interface"] not in interfaces_ip:
            continue
        ipv4_addresses = interfaces_ip[i["interface"]]["ipv4"]
        address, _, mask = i["ipaddress"].partition("/")
        if mask:
            prefix_length = int(mask)
        elif address not in ipv4_addresses:
            # EOS omits the mask on 'ip virtual-router address' -- infer it from the
            # interface subnet the address belongs to.
            prefix_length = _virtual_ip_prefix_length(ipv4_addresses, address)
        else:
            continue
        ipv4_addresses[address] = {"prefix_length": prefix_length, "is_virtual": True}

    for interface_name, interface_details in interfaces_ipv6_out.items():
        ipv6_list = []
        if interface_name not in interfaces_ip.keys():
            interfaces_ip[interface_name] = {}

        if "ipv4" not in interfaces_ip.get(interface_name):
            interfaces_ip[interface_name]["ipv4"] = {}
        if "ipv6" not in interfaces_ip.get(interface_name):
            interfaces_ip[interface_name]["ipv6"] = {}

        ipv6_list.append({
            "address": napalm.base.helpers.convert(
                napalm.base.helpers.ip,
                interface_details.get("linkLocal", {}).get("address"),
            ),
            "masklen": int(interface_details.get("linkLocal", {}).get("subnet", "::/0").split("/")[-1])
            # when no link-local set, address will be None and maslken 0
        })
        for address in interface_details.get("addresses"):
            ipv6_list.append({
                "address": napalm.base.helpers.ip(address.get("address")),
                "masklen": int(address.get("subnet").split("/")[-1]),
            })
        for ip in ipv6_list:
            if not ip.get("address"):
                continue
            if ip.get("address") not in interfaces_ip.get(interface_name).get("ipv6"):
                interfaces_ip[interface_name]["ipv6"][ip.get("address")] = {"prefix_length": ip.get("masklen")}

    for i in interface_acls:
        if i["interface"] in interfaces_ip.keys():
            interfaces_ip[i["interface"]]["interfaceacl"] = i["interfaceacl"]

    return interfaces_ip


def eos_get_static_routes(self):
    """Get static routes configured on EOS devices"""

    show_running_config_route = self.device.run_commands(['show running-config | section ip route'],
                                                         encoding='text')[0]['output']
    routes = _textfsm_extractor("eos_show_running_config_static_route", show_running_config_route)

    return routes


def eos_get_network_instances(self):
    """Get vrfs cnofigured on EOS devices, overwritten default function to use json output
    as it's the same across EOS versions"""

    instances = {}

    show_vrf_output = self.device.run_commands(["show vrf | json"])[0]["vrfs"]

    for vrf in show_vrf_output:
        instances[vrf] = {
            'name': vrf,
            'state': {
                'route_distinguisher': show_vrf_output[vrf]['routeDistinguisher']
            },
            'interfaces': {
                'interface': {i: {}
                              for i in show_vrf_output[vrf]['interfaces']}
            }
        }
        if vrf == 'default':
            instances[vrf]['type'] = 'DEFAULT_INSTANCE'
        else:
            instances[vrf]['type'] = 'L3VRF'

    return instances
