"""Fixtures for SAPN tests."""

from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURE = Path(__file__).parent / "fixture_nem12.csv"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Load custom_components/sapn with a real recorder (a hard dependency).

    recorder_mock must be requested before hass, so it comes first here.
    """
    return


@pytest.fixture
def nem12_text() -> str:
    """Synthetic NEM12 labelled in market time (UTC+10), 29 Aug to 28 Sep 2026."""
    return FIXTURE.read_text()
