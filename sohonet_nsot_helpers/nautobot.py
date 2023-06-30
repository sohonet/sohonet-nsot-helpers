import re
from nautobot_golden_config.models import FUNC_MAPPER




def sohonet_custom_compliance(obj):
    ''' Custom compliance function for use with nautobot golden config

        At the moment, this is used for compliance of interface configurations, and filters the configs
        so that only the interface description is being managed.

        This is a hacky first pass intended to be developed further, along the lines of
        https://github.com/joewesch/nautobot_golden_config_custom_compliance so we can define
        includes/exclude lines in the compliance rule within the nautobot UI

    '''


    # Modify with actual logic, this would always presume compliant.
    compliance_int = 1
    compliance = True
    ordered = True
    missing = ""
    extra = ""

    INTERFACE_PATTERN = re.compile("^interface")
    DESCRIPTION_PATTERN = re.compile("^\s+description")

    print('TESTING !!!!!!!')
    print(obj.rule)
    print(obj.device.platform.slug)

    if obj.rule.feature.name == 'interfaces' and obj.device.platform.slug == 'arista_eos':
        filtered_config = []
        for line in obj.actual.splitlines():
            interface_match = INTERFACE_PATTERN.search(line)
            description_match = DESCRIPTION_PATTERN.search(line)
            if interface_match or description_match:
                filtered_config.append(line)
        obj.actual = '\n'.join(filtered_config)

        compliance_method = FUNC_MAPPER['cli']
        print('SOHONET CUSTOM COMPLIANCE !!!!!!!!!!!!!!')
        compliance_details = compliance_method(obj)
        print(compliance_details)
        return compliance_details



    return {
        "compliance": compliance,
        "compliance_int": compliance_int,
        "ordered": ordered,
        "missing": missing,
        "extra": extra,
    }