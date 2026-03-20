import re
import logging

logger = logging.getLogger(__name__)


def compliance_include(compliance_include_patterns, actual_config):
    """
    Include lines from the actual configuration based on the provided patterns.
    """
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_include_patterns]
    for line in actual_config.splitlines():
        if any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)
    return included_lines

def compliance_stanza_extract(config_text, stanza_configs):
    """
    Extract config stanzas for one or more header/children combos.
    stanza_configs: list of dicts, each with:
        "header":   regex string matching stanza headers
        "children": list of regex strings matching desired child lines
    Results from all combos are merged in config order.

    Returns None when no stanzas match (sentinel: "nothing found").
    Callers should treat None differently from "" — see sohonet_custom_compliance
    for the intended handling logic.
    """
    if not config_text or not stanza_configs:
        return config_text or ""
    # Build compiled matchers for each combo
    combos = []
    for cfg in stanza_configs:
        header_pattern = cfg.get("header", "")
        child_patterns = cfg.get("children", [])
        if header_pattern and child_patterns:
            combos.append({
                "header_re": re.compile(header_pattern),
                "child_res": [re.compile(p) for p in child_patterns],
            })
    if not combos:
        return config_text
    lines = config_text.splitlines()
    # Track which output lines belong at which original position
    # so multiple combos merge in config order
    extracted = {}  # line_index -> list of output strings
    for combo in combos:
        header_re = combo["header_re"]
        child_res = combo["child_res"]
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if header_re.search(stripped):
                header = stripped
                header_idx = i
                i += 1
                matched_children = []
                while i < len(lines):
                    child_stripped = lines[i].strip()
                    if child_stripped == "!" or child_stripped == "":
                        break
                    # Check if this line is a header for ANY combo (stanza boundary)
                    if any(c["header_re"].search(child_stripped) for c in combos):
                        break
                    if any(cr.search(lines[i]) for cr in child_res):
                        matched_children.append(child_stripped)
                    i += 1
                if matched_children:
                    if header_idx not in extracted:
                        extracted[header_idx] = {"header": header, "children": []}
                    extracted[header_idx]["children"].extend(matched_children)
                if i < len(lines) and lines[i].strip() == "!":
                    i += 1
                continue
            i += 1
    # Build output in original config order
    if not extracted:
        return None
    out = []
    for idx in sorted(extracted.keys()):
        out.append(extracted[idx]["header"])
        for mc in extracted[idx]["children"]:
            out.append("    " + mc)
    return "\n".join(out)
    

def compliance_match_existence(patterns, actual_config, intended_config):
    """
    Check that lines matching patterns exist in actual config, without comparing values.
    Removes matched lines from BOTH configs so they're not compared by standard compliance.
    """
    flat_patterns = []
    for p in patterns:
        if isinstance(p, list):
            flat_patterns.extend(p)
        else:
            flat_patterns.append(p)
    matchers = [re.compile(pattern) for pattern in flat_patterns]
    missing_lines = []
    for matcher in matchers:
        intended_matches = [
            line for line in intended_config.splitlines()
            if matcher.search(line)
        ]
        actual_matches = [
            line for line in actual_config.splitlines()
            if matcher.search(line)
        ]
        if intended_matches and not actual_matches:
            missing_lines.extend(intended_matches)
    # Remove matched lines from BOTH configs
    modified_intended_lines = [
        line for line in intended_config.splitlines()
        if not any(m.search(line) for m in matchers)
    ]
    modified_actual_lines = [
        line for line in actual_config.splitlines()
        if not any(m.search(line) for m in matchers)
    ]
    modified_intended = "\n".join(modified_intended_lines)
    modified_actual = "\n".join(modified_actual_lines)
    all_exist = len(missing_lines) == 0
    return all_exist, missing_lines, modified_intended, modified_actual


def compliance_exclude(compliance_exclude_patterns, actual_config):
    """
    Exclude lines from the actual configuration based on the provided patterns.
    """
    included_lines = []
    matchers = [re.compile(pattern) for pattern in compliance_exclude_patterns]
    for line in actual_config.splitlines():
        if not any(matcher.search(line) for matcher in matchers):
            included_lines.append(line)
    return included_lines


def sohonet_custom_compliance(obj):
    """Custom compliance function for use with nautobot golden config"""
    from nautobot_golden_config.models import FUNC_MAPPER

    # If device role is CPE and NOT nautobot controlled, then ignore interface and shaping rules
    if obj.device.role.name == 'CPE' and not obj.device.cf['config_controlled'] and obj.rule.feature.name in [
        'interfaces', 'shaping', 'oam'
    ]:
        return {
            'compliance': True,
            'compliance_int': 1,
            'ordered': False,
            'missing': '',
            'extra': '',
        }

    # Store original intended before any modifications
    original_intended = obj.intended

    # Track any existence-only check failures
    existence_missing_lines = []

    logger.warning(f"=== START: intended lines: {len((obj.intended or '').splitlines())}, actual lines: {len((obj.actual or '').splitlines())} ===")

    stanza_config = obj.rule.custom_field_data.get("compliance_stanza_extract")
    if stanza_config:
        # Support both single dict (legacy) and list of dicts
        if isinstance(stanza_config, dict):
            stanza_config = [stanza_config]
        if isinstance(stanza_config, list):
            extracted_actual = compliance_stanza_extract(obj.actual or "", stanza_config)
            extracted_intended = compliance_stanza_extract(obj.intended or "", stanza_config)
            if extracted_intended is None:
                # Nothing in intended matches — nothing to enforce, skip stanza filtering
                logger.warning("=== STANZA EXTRACT: no intended stanzas matched, skipping stanza filter ===")
            else:
                obj.intended = extracted_intended
                # If actual had no matching stanzas, use "" so the diff engine
                # treats all intended stanzas as fully missing (correct remediation).
                obj.actual = extracted_actual if extracted_actual is not None else ""
            logger.warning(
                f"=== AFTER STANZA EXTRACT: intended lines: "
                f"{len((obj.intended or '').splitlines())}, "
                f"actual lines: {len((obj.actual or '').splitlines())} ==="
            )

    # Handle existence-only matching (e.g., RADIUS keys with device-generated hashes)
    compliance_existence_patterns = obj.rule.custom_field_data.get("compliance_match_existence")
    if compliance_existence_patterns and isinstance(compliance_existence_patterns, list):
        all_exist, missing, modified_intended, modified_actual = compliance_match_existence(
            compliance_existence_patterns,
            obj.actual or "",
            obj.intended or ""
        )
        if not all_exist:
            existence_missing_lines = missing
        # Store stripped versions for comparison only
        comparison_intended = modified_intended
        comparison_actual = modified_actual
    else:
        comparison_intended = None
        comparison_actual = None

    # Filter included lines from configs
    compliance_include_patterns = obj.rule.custom_field_data.get("compliance_include")
    if compliance_include_patterns and isinstance(compliance_include_patterns, list):
        included_lines_actual = compliance_include(compliance_include_patterns, obj.actual)
        included_lines_intended = compliance_include(compliance_include_patterns, obj.intended)
        obj.actual = "\n".join(included_lines_actual)
        obj.intended = "\n".join(included_lines_intended)
        logger.warning(f"=== AFTER INCLUDE: intended lines: {len((obj.intended or '').splitlines())}, actual lines: {len((obj.actual or '').splitlines())} ===")

    # Filter out excluded lines from configs
    compliance_exclude_patterns = obj.rule.custom_field_data.get("compliance_exclude")
    if compliance_exclude_patterns and isinstance(compliance_exclude_patterns, list):
        included_lines_actual = compliance_exclude(compliance_exclude_patterns, obj.actual)
        included_lines_intended = compliance_exclude(compliance_exclude_patterns, obj.intended)
        obj.actual = "\n".join(included_lines_actual)
        obj.intended = "\n".join(included_lines_intended)
        logger.warning(f"=== AFTER EXCLUDE: intended lines: {len((obj.intended or '').splitlines())}, actual lines: {len((obj.actual or '').splitlines())} ===")

    # Debug: compare configs before compliance
    logger.warning(f"=== INTENDED CONFIG HASH: {hash(obj.intended)} ===")
    logger.warning(f"=== ACTUAL CONFIG HASH: {hash(obj.actual)} ===")
    logger.warning(f"=== CONFIGS EQUAL: {obj.intended == obj.actual} ===")
    if obj.intended != obj.actual:
        intended_lines = set(obj.intended.splitlines())
        actual_lines = set(obj.actual.splitlines())
        logger.warning(f"=== ONLY IN INTENDED: {intended_lines - actual_lines} ===")
        logger.warning(f"=== ONLY IN ACTUAL: {actual_lines - intended_lines} ===")
    # Run compliance method with filtered configurations
    compliance_method = FUNC_MAPPER["cli"]
    # If existence matching stripped lines, use stripped versions for comparison only
    if comparison_intended is not None:
        saved_intended = obj.intended
        saved_actual = obj.actual
        obj.intended = comparison_intended
        obj.actual = comparison_actual
    
    compliance_details = compliance_method(obj)
    
    # Restore full configs so the model stores unstripped versions
    if comparison_intended is not None:
        obj.intended = saved_intended
        obj.actual = saved_actual

    logger.warning(f"=== COMPLIANCE RESULT: compliance={compliance_details.get('compliance')}, missing_len={len(compliance_details.get('missing', ''))}, extra_len={len(compliance_details.get('extra', ''))} ===")
    logger.warning(f"=== MISSING: {compliance_details.get('missing', '')[:200]} ===")
    logger.warning(f"=== EXTRA: {compliance_details.get('extra', '')[:200]} ===")

    # Restore obj.intended for config-sync
    # Only restore original intended if stanza extract was NOT applied
    # if not stanza_config:
    #    obj.intended = original_intended

    # Merge existence-check failures into the result
    if existence_missing_lines:
        existing_missing = compliance_details.get('missing', '')
        combined_missing = "\n".join(existence_missing_lines)
        if existing_missing:
            combined_missing = combined_missing + "\n" + existing_missing
        compliance_details['missing'] = combined_missing
        compliance_details['compliance'] = False
        compliance_details['compliance_int'] = 0

    logger.warning(f"=== FINAL RETURN: compliance={compliance_details.get('compliance')} ===")
    return compliance_details
