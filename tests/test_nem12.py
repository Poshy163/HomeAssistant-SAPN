"""NEM12 parser leniency: the header checks SAPN's portal download fails."""

import pytest

from custom_components.sapn.nem12 import Nem12Error, parse_nem12


def channel_values(text: str) -> dict:
    return {(c.nmi, c.suffix): c.values for c in parse_nem12(text)}


def test_file_without_header_reads_as_nem12(nem12_text):
    lines = nem12_text.splitlines(keepends=True)
    assert lines[0].startswith("100,")
    expected = channel_values(nem12_text)
    assert expected
    assert channel_values("".join(lines[1:])) == expected


def test_byte_order_mark_is_ignored(nem12_text):
    assert channel_values("\ufeff" + nem12_text) == channel_values(nem12_text)


@pytest.mark.parametrize(
    ("text", "message"),
    [("", "empty"), ("<!DOCTYPE html>\n<html></html>\n", "no 100 or 200 record")],
)
def test_non_nem12_is_rejected(text, message):
    with pytest.raises(Nem12Error, match=message):
        parse_nem12(text)
