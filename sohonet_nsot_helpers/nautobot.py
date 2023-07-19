import re


def compliance_include(compliance_include, actual_config):
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_include]
    for line in actual_config.splitlines():
        if any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)

    return included_lines


def sohonet_custom_compliance(obj):
    ''' Custom compliance function for use with nautobot golden config

        Custom field 'compliance_include' can be set in rules within the Nautobot UI.
        This should be a list of python regexs, only config lines matching these regexes will be


        At the moment, this is used for compliance of interface configurations, and filters the configs
        so that only the interface description is being managed.

        Based on https://github.com/joewesch/nautobot_golden_config_custom_compliance
    '''
    from nautobot_golden_config.models import FUNC_MAPPER

    # Filter included lines only from actual config
    compliance_include = obj.rule.custom_field_data.get("compliance_include")
    if compliance_include and isinstance(compliance_include, list):
        included_lines = compliance_include(obj.rule.custom_field_data.get("compliance_include"), obj.actual)
        obj.actual = '\n'.join(included_lines)

    # Run compliance method with filtered actual configuration
    compliance_method = FUNC_MAPPER['cli']
    compliance_details = compliance_method(obj)
    return compliance_details
