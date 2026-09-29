"""Config, reauth and options flows."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sapn.const import CONF_NMI, DOMAIN
from custom_components.sapn.portal import SapnAuthError

LOGIN = "custom_components.sapn.config_flow.login_and_list_nmis"


async def test_single_nmi_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch(LOGIN, return_value=["20012345678"]), patch("custom_components.sapn.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SAPN 20012345678"
    assert result["data"] == {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw", CONF_NMI: "20012345678"}
    assert result["result"].unique_id == "2001234567"


async def test_bad_password_shows_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch(LOGIN, side_effect=SapnAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_multiple_nmis_asks_which(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch(LOGIN, return_value=["20012345678", "20099999990"]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"}
        )
    assert result["step_id"] == "nmi"
    with patch("custom_components.sapn.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NMI: "20099999990"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NMI] == "20099999990"


async def test_reauth_updates_password(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="2001234567",
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "old", CONF_NMI: "20012345678"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(LOGIN, return_value=["20012345678"]), patch("custom_components.sapn.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_options_validate_run_times(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="2001234567",
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw", CONF_NMI: "20012345678"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"days_back": 7, "run_times": "ten past ten"}
    )
    assert result["errors"] == {"run_times": "invalid_run_times"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"days_back": 7, "run_times": "10:15,22:15"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"days_back": 7, "run_times": "10:15,22:15"}
