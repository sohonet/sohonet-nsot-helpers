"""Tests for compliance_stanza_extract.

This function decides which config lines Golden Config compares, so a bug here
shows up as phantom drift and can drive a remediation push at config that isn't
really wrong. Run with: python -m pytest tests/
"""

from sohonet_nsot_helpers.nautobot import compliance_stanza_extract

# The live AOS-CX 'interfaces' rule.
AOSCX_RULE = [
    {
        "header": r"^interface\s+(?:\d+/\d+/\d+|lag \d+)",
        "children": [
            r"^\s+rate-limit broadcast",
            r"^\s+rate-limit unknown-unicast",
            r"^\s+loop-protect",
        ],
    }
]

# Trimmed from a real AOS-CX backup. The shape that matters: `interface persona
# access` follows the last numbered port, is not matched by the rule's header
# pattern, and carries loop-protect children of its own.
AOSCX_CONFIG = """hostname SW1
interface 1/1/1
    no shutdown
    rate-limit unknown-unicast 10000 kbps
    rate-limit broadcast 10000 kbps
    loop-protect
    loop-protect action tx-rx-disable
interface 1/1/52
    no shutdown
    vlan access 1
    rate-limit unknown-unicast 10000 kbps
    rate-limit broadcast 10000 kbps
interface persona access
    no shutdown
    vlan trunk allowed 4002
    loop-protect
    loop-protect action tx-rx-disable
interface vlan 402
    description DCN-IF
    ip address 10.32.64.163/24
snmp-server community public
    access-level rw
"""


def _stanza(text, header):
    """Return the lines attributed to one stanza in the extractor's output."""
    out, collecting = [], False
    for line in (text or "").splitlines():
        if line == header:
            collecting = True
            continue
        if collecting:
            if line[:1] not in (" ", "\t"):
                break
            out.append(line.strip())
    return out


class TestStanzaBoundaries:
    def test_sibling_block_children_are_not_absorbed(self):
        """A stanza must not run on into a block the header pattern misses.

        `interface persona access` matches neither the header pattern nor the
        `!`/blank terminators, so the last numbered port used to swallow its
        loop-protect lines and compliance reported them as extra config on a
        port that has none.
        """
        got = compliance_stanza_extract(AOSCX_CONFIG, AOSCX_RULE)
        assert _stanza(got, "interface 1/1/52") == [
            "rate-limit unknown-unicast 10000 kbps",
            "rate-limit broadcast 10000 kbps",
        ]

    def test_real_children_are_still_matched(self):
        got = compliance_stanza_extract(AOSCX_CONFIG, AOSCX_RULE)
        assert _stanza(got, "interface 1/1/1") == [
            "rate-limit unknown-unicast 10000 kbps",
            "rate-limit broadcast 10000 kbps",
            "loop-protect",
            "loop-protect action tx-rx-disable",
        ]

    def test_unmatched_blocks_are_not_emitted_at_all(self):
        got = compliance_stanza_extract(AOSCX_CONFIG, AOSCX_RULE) or ""
        assert "persona" not in got
        assert "interface vlan 402" not in got
        assert "snmp-server" not in got

    def test_bang_terminator_still_works(self):
        config = (
            "interface 1/1/1\n    rate-limit broadcast 10000 kbps\n!\n"
            "interface 1/1/2\n    rate-limit broadcast 20000 kbps\n!\n"
        )
        got = compliance_stanza_extract(config, AOSCX_RULE)
        assert _stanza(got, "interface 1/1/1") == ["rate-limit broadcast 10000 kbps"]
        assert _stanza(got, "interface 1/1/2") == ["rate-limit broadcast 20000 kbps"]

    def test_adjacent_matching_headers_still_split(self):
        config = (
            "interface 1/1/1\n    rate-limit broadcast 10000 kbps\n"
            "interface 1/1/2\n    rate-limit broadcast 20000 kbps\n"
        )
        got = compliance_stanza_extract(config, AOSCX_RULE)
        assert _stanza(got, "interface 1/1/1") == ["rate-limit broadcast 10000 kbps"]
        assert _stanza(got, "interface 1/1/2") == ["rate-limit broadcast 20000 kbps"]


class TestSentinels:
    def test_no_matching_stanzas_returns_none(self):
        """None is the documented "nothing found" sentinel, distinct from ""."""
        assert compliance_stanza_extract("hostname SW1\n", AOSCX_RULE) is None

    def test_empty_config_returns_empty_string(self):
        assert compliance_stanza_extract("", AOSCX_RULE) == ""

    def test_no_rule_returns_config_unchanged(self):
        assert compliance_stanza_extract(AOSCX_CONFIG, []) == AOSCX_CONFIG


class TestRequirePredicate:
    RULE = [
        {
            "header": r"^interface\s+\d+/\d+/\d+",
            "children": [r"^\s+rate-limit broadcast"],
            "require": [r"^\s+description NB-"],
        }
    ]
    CONFIG = (
        "interface 1/1/1\n    description NB-managed\n    rate-limit broadcast 1 kbps\n"
        "interface 1/1/2\n    description manual\n    rate-limit broadcast 2 kbps\n"
    )

    def test_keeps_stanza_with_marker(self):
        got = compliance_stanza_extract(self.CONFIG, self.RULE)
        assert "interface 1/1/1" in got

    def test_drops_stanza_without_marker(self):
        got = compliance_stanza_extract(self.CONFIG, self.RULE)
        assert "interface 1/1/2" not in got

    def test_require_is_not_confused_by_a_sibling_block(self):
        """The marker must be found in the stanza's own body, not a neighbour's."""
        config = (
            "interface 1/1/2\n    rate-limit broadcast 2 kbps\n"
            "interface persona access\n    description NB-managed\n"
        )
        assert compliance_stanza_extract(config, self.RULE) is None
