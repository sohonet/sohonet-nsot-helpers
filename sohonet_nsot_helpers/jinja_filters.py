# Sohonet custom Jinja2 Filters
from passlib.hash import cisco_type7
import base64
from netutils.vlan import vlanlist_to_config
import requests
import ipaddress
import re
import math


def encrypt_cisco_type7(password):
    return cisco_type7.hash(password, salt=1)


def encrypt_netiron_snmp(community):

    # yapf: disable
    table = {
      'a': '!','b': '2','c': 'd','d': '@','e': 'n','f': 'G','g': '"','h': 'b','i': '=','j': '?','k': 'D','l': '^','m': '6','n': 'g','o': 's','p': 'S',
      'q': 'R','r': 'U','s': '-','t': 'o','u': 'i','v': 'r','w': '+','x': 'C','y': '\\','z': 'x','A': 'q','B': 'L','C': 'B','D': ':','E': 'f','F': 'w',
      'G': '9','H': '0','I': """'""",'J': 'c','K': 'h','L': '#','M': 'k','N': 't','O': ',','P': '5','Q': '_','R': 'P','S': '%','T': 'l','U': 'K','V': ']',
      'W': 'a','X': 'E','Y': '*','Z': '(','0': 'Q','1': 'Z','2': '|','3': '8','4': '3','5': '0','6': 'm','7': 'Y','8': 'W','9': '{','!': 'V','"': 'F',
      '#': 'I','$': '4','%': 'u','&': '>',"""'""": 'N','(': 'p',')': 'z','*': 'X','+': 'e',',': 'T','-': 'M','.': '&','/': ')',':': 'y',';': ';','<': '`',
      '=': '$','>': 'v','@': '1','[': '7',']': '~','\\': 'H','^': '<','_': '}','`': '/','{': '.','}': 'j','~': '[',
    }
    # yapf: enable

    cipher_string = ''
    for char in community:
        cipher_string += table[char]

    base64_bytes = base64.b64encode(cipher_string.encode('ascii'))
    return '$' + base64_bytes.decode('ascii')


def mrv_physical_interfaces_to_config(ports):
    ''' convert list of dicts containing interface names to a config line, filtering for numbered ports only

        i.e. 1-16  or  1-13,15-16
    '''
    portlist = []
    trunklist = []
    for port in ports:
        if port['type'] != 'VIRTUAL' and port['name'].isdigit():
            if port['lag']:
                if port['lag']['name'] not in trunklist:
                    trunklist.append(port['lag']['name'])
            else:
                portlist.append(int(port['name']))

    if not portlist:
        return ''

    portlist_config = vlanlist_to_config(portlist, min_grouping_size=2)
    if trunklist:
        portlist_config[0] = portlist_config[0] + "," + ",".join(sorted(trunklist))
    return portlist_config[0]


def is_smn_ip(ipaddr):
    ''' check if an ipaddress is in SMN ranges '''
    req = requests.get(
        'https://lon-proxy-03.storagesvc.sohonet.com/v1/AUTH_bc8242fea43146a7b8cee34a40f328e0/ip-ranges-PUBLIC-READABLE/smn-ip-ranges.json'
    )
    ip_ranges = req.json()

    ip_to_check = ipaddress.ip_address(ipaddr.split('/')[0])

    for prefix in ip_ranges['prefixes']:
        if ip_to_check in ipaddress.ip_network(prefix['ip_prefix']):
            return True

    return False


def netiron_normalized_interface_to_config(interface_name):
    ''' Convert Netiron normalized interface names to names used in config

    NORMALIZED                      CONFIG

    GigabitEthernet1        ->      ethernet 1
    GigabitEthernet1/24     ->      ethernet 1/24
    10GigabitEthernet2/22   ->      ethernet 2/22
    Ve1732                  ->      ve 1732
    Loopback1               ->      loopback 1
    Ethernetmgmt1           ->      management 1

    '''

    match = re.match(r'\d*(\D+)(\d+[\d|/]*)', interface_name)
    if match:
        if 'mgmt' in match.group(1):
            return f"management {match.group(2)}"
        elif 'Ethernet' in match.group(1):
            return f"ethernet {match.group(2)}"
        elif 'Ve' in match.group(1):
            return f"ve {match.group(2)}"
        elif 'Loopback' in match.group(1):
            return f"loopback {match.group(2)}"

    return interface_name


def bandwith_to_optiswitch_name(bandwidth):
    ''' convert an int for megabits and convert to multiples in m or g
    i.e.

        500 -> 500m
        1000 -> 1g
        1500 -> 1500m
        3000 -> 3g
        10000 -> 10g
    '''

    if (bandwidth % 1000) == 0:
        return f"{int(bandwidth/1000)}g"
    else:
        return f"{bandwidth}m"


def adva_shaping_values(bandwidth, custom_shaping=False, shaping_eir=False):
    ''' return shaping values for the given bandwidth '''

    bandwidth_params = {
        10000: {
            "cir": 9999360000,
            "eir": 128000,
            "cbs": 1536,
            "ebs": 16,
            "buffersize": 1536
        },
        9000: {
            "cir": 8999360000,
            "eir": 128000,
            "cbs": 1536,
            "ebs": 16,
            "buffersize": 1536
        },
        8000: {
            "cir": 7999360000,
            "eir": 128000,
            "cbs": 1536,
            "ebs": 16,
            "buffersize": 1536
        },
        7000: {
            "cir": 6999360000,
            "eir": 128000,
            "cbs": 1536,
            "ebs": 16,
            "buffersize": 1536
        },
        6000: {
            "cir": 5999360000,
            "eir": 128000,
            "cbs": 1536,
            "ebs": 16,
            "buffersize": 1536
        },
        5000: {
            "cir": 4999360000,
            "eir": 128000,
            "cbs": 1536,
            "ebs": 16,
            "buffersize": 1536
        },
        4000: {
            "cir": 3999360000,
            "eir": 128000,
            "cbs": 1280,
            "ebs": 16,
            "buffersize": 1280
        },
        3000: {
            "cir": 2999360000,
            "eir": 128000,
            "cbs": 1280,
            "ebs": 16,
            "buffersize": 1280
        },
        2000: {
            "cir": 1999360000,
            "eir": 128000,
            "cbs": 1280,
            "ebs": 16,
            "buffersize": 1280
        },
        1000: {
            "cir": 999360000,
            "eir": 128000,
            "cbs": 1024,
            "ebs": 16,
            "buffersize": 1024
        },
        900: {
            "cir": 899328000,
            "eir": 128000,
            "cbs": 1024,
            "ebs": 16,
            "buffersize": 1024
        },
        800: {
            "cir": 799360000,
            "eir": 128000,
            "cbs": 1024,
            "ebs": 16,
            "buffersize": 1024
        },
        700: {
            "cir": 699328000,
            "eir": 128000,
            "cbs": 512,
            "ebs": 16,
            "buffersize": 512
        },
        600: {
            "cir": 599360000,
            "eir": 128000,
            "cbs": 512,
            "ebs": 16,
            "buffersize": 512
        },
        500: {
            "cir": 499328000,
            "eir": 128000,
            "cbs": 512,
            "ebs": 16,
            "buffersize": 512
        },
        400: {
            "cir": 399360000,
            "eir": 128000,
            "cbs": 256,
            "ebs": 16,
            "buffersize": 256
        },
        300: {
            "cir": 299328000,
            "eir": 128000,
            "cbs": 256,
            "ebs": 16,
            "buffersize": 256
        },
        200: {
            "cir": 199360000,
            "eir": 128000,
            "cbs": 128,
            "ebs": 16,
            "buffersize": 128
        },
        100: {
            "cir": 99328000,
            "eir": 128000,
            "cbs": 128,
            "ebs": 16,
            "buffersize": 128
        },
        50: {
            "cir": 49344000,
            "eir": 128000,
            "cbs": 64,
            "ebs": 16,
            "buffersize": 64
        },
        # 64k - used for overcommited services.
        64: {
            "cir": 64000,
            "eir": 128000,
            "cbs": 1024,
            "ebs": 16,
            "buffersize": 1024
        }
    }

    # Round down to nearest bandwidth value
    if bandwidth not in bandwidth_params.keys():
        # 10000 is max bandiwdth in bandwidth_params
        if bandwidth > 10000:
            bandwidth = 10000
        # Check thousands
        elif math.floor(bandwidth / 1000) * 1000 in bandwidth_params.keys():
            bandwidth = math.floor(bandwidth / 1000) * 1000
        # Check hundreds
        elif math.floor(bandwidth / 100) * 100 in bandwidth_params.keys():
            bandwidth = math.floor(bandwidth / 100) * 100
        # Only remaining value is 50
        else:
            bandwidth = 50

    shaping_table = bandwidth_params[bandwidth]

    # Support custom EIR values - for overcommited services with low cir & high eir.
    # Return modified table
    if custom_shaping and shaping_eir:
        eir_shaping_table = bandwidth_params[shaping_eir]
        shaping_table['eir'] = eir_shaping_table['cir']
        shaping_table['cbs'] = eir_shaping_table['cbs']
        shaping_table['ebs'] = eir_shaping_table['ebs']
        shaping_table['buffersize'] = eir_shaping_table['buffersize']

    return shaping_table


def service_types(service_inventories):
    ''' from list of service inventories, return a list of service types '''

    return [s['serviceid']['service_type'] for s in service_inventories]


def has_p2p(service_inventories):
    ''' returns true if list of service inventories include a managed P2P service'''
    service_types = service_types(service_inventories)
    if 'Network - P2P Managed' in service_types:
        return True

    return False
