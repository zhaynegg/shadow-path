"""Reading heights out of OSM tags, which are typed by hand and show it."""
import numpy as np
import pytest

from backend.core.buildings import parse_numeric


@pytest.mark.parametrize("raw, expected", [
    ("12", 12.0),
    ("12 m", 12.0),
    ("3,5", 3.5),      # comma decimal, which is how it is written locally
    ("  7.5  ", 7.5),
    ("24m", 24.0),
])
def test_reads_the_leading_number(raw, expected):
    assert parse_numeric(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [
    None,
    float("nan"),
    "ё",                              # a keyboard slip, twice over in Astana
    "Многофункциональный комплекс",   # a description typed into a numeric field
    "",
])
def test_unreadable_is_missing_not_zero(raw):
    """nan, so the height chain falls through to the next source.

    Returning 0.0 here would be worse than returning nothing: it is a number,
    so it wins the chain, and it means a building of no height.
    """
    assert np.isnan(parse_numeric(raw))


@pytest.mark.parametrize("raw", ["0", "0 m", "0.0", "0,0"])
def test_zero_is_missing_not_a_measurement(raw):
    """The bug this function exists to not have.

    Four Astana buildings carry `height=0` and a fifth `building:levels=0`, and
    none of the five has another tag to fall back on. Parsed as 0.0 the value
    ranks as a measurement -- above the prior, above the fallback, beaten only
    by a hand survey -- so a hotel and three apartment blocks stood 0 m tall and
    cast no shadow anywhere on the map. Nothing errored; they were simply gone.
    """
    assert np.isnan(parse_numeric(raw))
