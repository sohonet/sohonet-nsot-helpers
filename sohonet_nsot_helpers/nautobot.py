import re


def compliance_include(compliance_include_patterns, actual_config):
    """
    Include lines from the actual configuration based on the provided patterns.

    Args:
        compliance_include_patterns (list): List of regex patterns to include.
        actual_config (str): The actual configuration as a string.

    Returns:
        list: Lines from the actual configuration that match any of the include patterns.
    """
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_include_patterns]
    for line in actual_config.splitlines():
        if any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)

    return included_lines


def compliance_exclude(compliance_exclude_patterns, actual_config):
    """
    Exclude lines from the actual configuration based on the provided patterns.

    Args:
        compliance_exclude_patterns (list): List of regex patterns to exclude.
        actual_config (str): The actual configuration as a string.

    Returns:
        list: Lines from the actual configuration that do not match any of the exclude patterns.
    """
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_exclude_patterns]
    for line in actual_config.splitlines():
        if not any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)

    return included_lines


def sohonet_custom_compliance(obj):
    """Custom compliance function for use with nautobot golden config

    Custom field 'compliance_include' and 'compliance_exclude' can be set in rules within the
    Nautobot UI. This should be a list of python regexs, config lines matching these regexes
    will be included/excluded from the compliance check as necessaary,

    Additionally, if the device is a CPE and not nautobot controlled, then the compliance check will
    always succeed for the following features:
    - interfaces
    - shaping
    - oam

    This is to support partial config management for devices that are not fully managed by nautobot.

    Based on https://github.com/joewesch/nautobot_golden_config_custom_compliance
    """
    from nautobot_golden_config.models import FUNC_MAPPER

    # If device role is CPE and NOT nautobot controlled, then ignore interface and shaping rules
    # This is to allow old MRVs which are are not managing to have base config rules (i.e. syslog, ntp)
    # But not include the full service config management
    if obj.device.role.name == 'CPE' and not obj.device.cf['config_controlled'] and obj.rule.feature.name in ['interfaces', 'shaping', 'oam']:
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
