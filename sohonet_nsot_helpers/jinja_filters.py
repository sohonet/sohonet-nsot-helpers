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

def adva_shaping_values(bandwidth, value):
    ''' return a shaper value for the given bandwidth '''

    bandwidth_params = {
        10000: {
            "policer_cir": 9999872000,
            "policer_eir": 128000,
            "policer_cbs": 1536,
            "policer_ebs": 16,
            "shaper_cir": 9999872000,
            "shaper_eir": 128,
            "shaper_bs": 1536
        },
        9000: {
            "policer_cir": 8999872000,
            "policer_eir": 128000,
            "policer_cbs": 1536,
            "policer_ebs": 16,
            "shaper_cir": 8999872000,
            "shaper_eir": 128,
            "shaper_bs": 1536
        },
        8000: {
            "policer_cir": 7999872000,
            "policer_eir": 128000,
            "policer_cbs": 1536,
            "policer_ebs": 16,
            "shaper_cir": 7999872000,
            "shaper_eir": 128,
            "shaper_bs": 1536
        },
        7000: {
            "policer_cir": 6999872000,
            "policer_eir": 128000,
            "policer_cbs": 1536,
            "policer_ebs": 16,
            "shaper_cir": 6999872000,
            "shaper_eir": 128,
            "shaper_bs": 1536
        },
        6000: {
            "policer_cir": 5999872000,
            "policer_eir": 128000,
            "policer_cbs": 1536,
            "policer_ebs": 16,
            "shaper_cir": 5999872000,
            "shaper_eir": 128,
            "shaper_bs": 1536
        },
        5000: {
            "policer_cir": 4999872000,
            "policer_eir": 128000,
            "policer_cbs": 1536,
            "policer_ebs": 16,
            "shaper_cir": 4999872000,
            "shaper_eir": 128,
            "shaper_bs": 1536
        },
        4000: {
            "policer_cir": 3999872000,
            "policer_eir": 128000,
            "policer_cbs": 1280,
            "policer_ebs": 16,
            "shaper_cir": 3999872000,
            "shaper_eir": 128,
            "shaper_bs": 1280
        },
        3000: {
            "policer_cir": 2999872000,
            "policer_eir": 128000,
            "policer_cbs": 1280,
            "policer_ebs": 16,
            "shaper_cir": 2999872000,
            "shaper_eir": 128,
            "shaper_bs": 1280
        },
        2000: {
            "policer_cir": 1999872000,
            "policer_eir": 128000,
            "policer_cbs": 1280,
            "policer_ebs": 16,
            "shaper_cir": 1999872000,
            "shaper_eir": 128,
            "shaper_bs": 1280
        },
        1000: {
            "policer_cir": 999872000,
            "policer_eir": 128000,
            "policer_cbs": 1024,
            "policer_ebs": 16,
            "shaper_cir": 999872000,
            "shaper_eir": 128,
            "shaper_bs": 1024
        },
        900: {
            "policer_cir": 899872000,
            "policer_eir": 128000,
            "policer_cbs": 1024,
            "policer_ebs": 16,
            "shaper_cir": 899872000,
            "shaper_eir": 128000,
            "shaper_bs": 1024
        },
        800: {
            "policer_cir": 799872000,
            "policer_eir": 128000,
            "policer_cbs": 1024,
            "policer_ebs": 16,
            "shaper_cir": 799872000,
            "shaper_eir": 128000,
            "shaper_bs": 1024
        },
        700: {
            "policer_cir": 699872000,
            "policer_eir": 128000,
            "policer_cbs": 512,
            "policer_ebs": 16,
            "shaper_cir": 699872000,
            "shaper_eir": 128000,
            "shaper_bs": 512
        },
        600: {
            "policer_cir": 599872000,
            "policer_eir": 128000,
            "policer_cbs": 512,
            "policer_ebs": 16,
            "shaper_cir": 599872000,
            "shaper_eir": 128000,
            "shaper_bs": 512
        },
        500: {
            "policer_cir": 499872000,
            "policer_eir": 128000,
            "policer_cbs": 512,
            "policer_ebs": 16,
            "shaper_cir": 499872000,
            "shaper_eir": 128000,
            "shaper_bs": 512
        },
        400: {
            "policer_cir": 399872000,
            "policer_eir": 128000,
            "policer_cbs": 256,
            "policer_ebs": 16,
            "shaper_cir": 399872000,
            "shaper_eir": 128000,
            "shaper_bs": 256
        },
        300: {
            "policer_cir": 299872000,
            "policer_eir": 128000,
            "policer_cbs": 256,
            "policer_ebs": 16,
            "shaper_cir": 299872000,
            "shaper_eir": 128000,
            "shaper_bs": 256
        },
        200: {
            "policer_cir": 199872000,
            "policer_eir": 128000,
            "policer_cbs": 128,
            "policer_ebs": 16,
            "shaper_cir": 199872000,
            "shaper_eir": 128000,
            "shaper_bs": 128
        },
        100: {
            "policer_cir": 99872000,
            "policer_eir": 128000,
            "policer_cbs": 128,
            "policer_ebs": 16,
            "shaper_cir": 99872000,
            "shaper_eir": 128000,
            "shaper_bs": 128
        },
        50: {
            "policer_cir": 49920000,
            "policer_eir": 128000,
            "policer_cbs": 64,
            "policer_ebs": 16,
            "shaper_cir": 49872000,
            "shaper_eir": 128000,
            "shaper_bs": 64
        }
    }

    # Round down to nearest bandwidth value
    if bandwidth not in bandwidth_params.keys():
        # 10000 is max bandiwdth in bandwidth_params
        if bandwidth > 10000:
            bandwidth = 10000
        # Check thousands
        elif math.floor(bandwidth/1000) * 1000 in bandwidth_params.keys():
            bandwidth = math.floor(bandwidth/1000) * 1000
        # Check hundreds
        elif math.floor(bandwidth/100) * 100 in bandwidth_params.keys():
            bandwidth = math.floor(bandwidth/100) * 100
        # Only remaining value is 50
        else:
            bandwidth = 50

    return bandwidth_params[bandwidth][value]