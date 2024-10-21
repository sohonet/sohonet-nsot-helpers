import re


def compliance_include(compliance_include_patterns, actual_config):
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_include_patterns]
    for line in actual_config.splitlines():
        if any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)

    return included_lines


def compliance_exclude(compliance_exclude_patterns, actual_config):
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_exclude_patterns]
    for line in actual_config.splitlines():
        if not any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)

    return included_lines


def sohonet_custom_compliance(obj):
    """Custom compliance function for use with nautobot golden config

    Custom field 'compliance_include' can be set in rules within the Nautobot UI.
    This should be a list of python regexs, only config lines matching these regexes will be


    At the moment, this is used for compliance of interface configurations, and filters the configs
    so that only the interface description is being managed.

    Based on https://github.com/joewesch/nautobot_golden_config_custom_compliance
    """
    from nautobot_golden_config.models import FUNC_MAPPER

    # If device role is CPE and NOT nautobot controlled, then ignore interface and shaping rules
    # This is to allow old MRVs which are are not managing to have base config rules (i.e. syslog, ntp)
    # But not include the full service config management
    if obj.device.role.name == 'CPE' and not obj.device.cf['config_controlled'] and obj.rule.feature.name in ['interfaces', 'shaping']:
        return {
            'compliance': True,
            'compliance_int': 1,
            'ordered': False,
            'missing': '',
            'extra': '',
        }


    # Filter included lines only from actual config
    compliance_include_patterns = obj.rule.custom_field_data.get("compliance_include")
    if compliance_include_patterns and isinstance(compliance_include_patterns, list):
        included_lines_actual = compliance_include(compliance_include_patterns, obj.actual)
        included_lines_intended = compliance_include(compliance_include_patterns, obj.intended)
        obj.actual = "\n".join(included_lines_actual)
        obj.intended = "\n".join(included_lines_intended)

    # Filter out excluded lines only from actual config
    compliance_exclude_patterns = obj.rule.custom_field_data.get("compliance_exclude")
    if compliance_exclude_patterns and isinstance(compliance_exclude_patterns, list):
        included_lines_actual = compliance_exclude(compliance_exclude_patterns, obj.actual)
        included_lines_intended = compliance_exclude(compliance_exclude_patterns, obj.intended)
        obj.actual = "\n".join(included_lines_actual)
        obj.intended = "\n".join(included_lines_intended)

    # Run compliance method with filtered actual configuration
    compliance_method = FUNC_MAPPER["cli"]
    compliance_details = compliance_method(obj)
    return compliance_details
