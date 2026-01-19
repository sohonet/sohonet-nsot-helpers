# sohonet_nsot_helpers/napalm/arubacx_helpers.py

from nornir_napalm.plugins.tasks import napalm_get


class ArubaCXDriver:
    """
    Driver shim to mirror the EOSDriver pattern so we can monkeypatch
    getter functions similarly if needed in future.
    """
    pass


def arubacx_get_interfaces(task):
    """
    Return a dict compatible with NAPALM 'interfaces' getter output.

    Example shape:
    {
        "1/1/1": {
            "is_enabled": True,
            "description": "Uplink",
            "last_flapped": 0.0,
            "mtu": 1500,
            "speed": 10000,
            "mac_address": "00:11:22:33:44:55",
            "type": None,
            "children": [],
            "ve_children": [],
        },
        "vlan10": { ... },
        "lag1": { ... },
    }
    """
    result = napalm_get(task, getters=["interfaces"])
    interfaces = result.result.get("interfaces", {})

    # Normalize to ensure downstream code can rely on keys existing
    for name, data in interfaces.items():
        data.setdefault("children", [])
        data.setdefault("ve_children", [])
        data.setdefault("type", None)

    return interfaces


def arubacx_get_interfaces_ip(task):
    """
    Return dict compatible with NAPALM 'interfaces_ip'.

    Example shape:
    {
        "vlan10": {
            "ipv4": {
                "192.0.2.10": {"prefix_length": 24}
            },
            "ipv6": {}
        }
    }
    """
    result = napalm_get(task, getters=["interfaces_ip"])
    return result.result.get("interfaces_ip", {})


def arubacx_get_facts(task):
    """
    Return dict compatible with NAPALM 'facts'.

    Example keys used:
    - serial_number
    - hostname
    - model
    - os_version
    """
    result = napalm_get(task, getters=["facts"])
    return result.result.get("facts", {})
