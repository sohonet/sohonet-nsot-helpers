import re


def interface_type(interface, speed, interface_type=False):
    ''' Return a dict containing netbox interface type name and slug based on interface name and speed
    Arista virtual interfaces: Ethernet4.420, Loopback1, Vlan3878
    NetIron vitual interfaces: loopback1, ve149, Tunnel1
    MRV virtual interfaces: vif461, t1
    Arista lags: Port-Channel10
    NetIron lags: lag1
    We are making some assumptions about the actual physical media here, but it's
    good enough for now
    '''
    # If interface type is available, use it
    if interface_type == 'virtual':
        return {'name': 'Virtual', 'slug': 'virtual'}
    elif interface_type == '1000base-t':
        return {'name': '1000BASE-T (1GE)', 'slug': '1000base-t'}
    elif interface_type == '10gbase-x-sfpp':
        return {'name': 'SFP+ (10GE)', 'slug': '10gbase-x-sfpp'}
    elif interface_type == '10gbase-cx4':
        return {'name': '10GBASE-CX4 (10GE)', 'slug': '10gbase-cx4'}
    elif interface_type == '10gbase-t':
        return {'name': '10GBASE-T (10GE)', 'slug': '10gbase-t'}
    elif interface_type == '25gbase-x-sfp28':
        return {'name': 'SFP28 (25GE)', 'slug': '25gbase-x-sfp28'}
    elif interface_type == '40gbase-x-qsfpp':
        return {'name': 'QSFP+ (40GE)', 'slug': '40gbase-x-qsfpp'}
    elif interface_type == '100gbase-x-qsfp28':
        return {'name': 'QSFP28 (100GE)', 'slug': '100gbase-x-qsfp28'}
    elif interface_type:
        return {'name': interface_type, 'slug': interface_type}

    # loopback/tunnel/ve interfaces
    if re.match(r'^loopback|ve|tunnel|vlan|vif|default_vlan|lo|null', interface.lower()):
        return {'name': 'Virtual', 'slug': 'virtual'}
    # Arista EthernetX.YYY interfaces
    if re.match(r'^(ethernet|port-channel)\d+.*\.\d+', interface.lower()):
        return {'name': 'Virtual', 'slug': 'virtual'}

    # NetIron, Arista, MRV LAGs
    if re.match(r'^lag\d+|^port-channel\d+|^t\d+|^trk\d+', interface.lower()):
        return {'name': 'Link Aggregation Group (LAG)', 'slug': 'lag'}

    if speed == 1000:
        return {'name': '1000BASE-T (1GE)', 'slug': '1000base-t'}
    elif speed == 10000:
        return {'name': 'SFP+ (10GE)', 'slug': '10gbase-x-sfpp'}
    elif speed == 25000:
        return {'name': 'SFP28 (25GE)', 'slug': '25gbase-x-sfp28'}
    elif speed == 40000:
        return {'name': 'QSFP+ (40GE)', 'slug': '40gbase-x-qsfpp'}
    elif speed == 100000:
        return {'name': 'QSFP28 (100GE)', 'slug': '100gbase-x-qsfp28'}

    # A default value in all other cases
    return {'name': '1000BASE-T (1GE)', 'slug': '1000base-t'}
