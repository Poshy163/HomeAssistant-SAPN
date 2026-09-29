"""Client for SA Power Networks' Your Meter Data portal.

Adapted from sapnmeterdata 0.3.3 (https://github.com/bfulham/sapnmeterdata),
trimmed to login, NMI discovery and the raw NEM12 download, with BeautifulSoup,
pandas and nemreader removed so the integration needs no extra packages.
Blocking: call these methods from an executor.

MIT License

Copyright (c) 2026 Brady Fulham

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime, time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://customer.portal.sapowernetworks.com.au/meterdata/"
SERVICE_BASE = "https://customer.portal.sapowernetworks.com.au/"
LOGIN_PAGE = "CADSiteLogin"
FORM_PREFIX = "loginPage:SiteTemplate:siteLogin:loginComponent:loginForm"
VIEW_STATE = "com.salesforce.visualforce.ViewState"
VIEW_STATE_MAC = "com.salesforce.visualforce.ViewStateMAC"
DEFAULT_TIMEOUT = 30


class SapnError(Exception):
    """Base error for the SAPN portal."""


class SapnAuthError(SapnError):
    """SAPN rejected the login or asked for an extra verification step."""


class SapnNoDataError(SapnError):
    """SAPN has no NEM12 data for the requested NMI and period."""


class _HiddenInputs(HTMLParser):
    """Collect hidden <input> values by id."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        attributes = {k.lower(): v for k, v in attrs}
        if (attributes.get("type") or "").lower() == "hidden" and attributes.get("id"):
            self.values[attributes["id"]] = attributes.get("value") or ""

    handle_startendtag = handle_starttag


def hidden_inputs(document: str) -> dict[str, str]:
    """Return hidden input values keyed by element id."""
    parser = _HiddenInputs()
    parser.feed(document)
    parser.close()
    return parser.values


def looks_like_login_page(document: str) -> bool:
    """Return True when a response contains the SAPN login form."""
    return (
        "loginComponent:loginForm:username" in document
        and "loginComponent:loginForm:password" in document
    )


def extract_redirect(document: str) -> str | None:
    """Extract the Salesforce post-login redirect from a Visualforce response."""
    match = re.search(r"""\.handleRedirect\(\s*['"](?P<url>[^'"]+)['"]\s*\)""", document)
    return html.unescape(match.group("url")) if match else None


def _iter_json_objects(document: str) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", document):
        try:
            candidate, _ = decoder.raw_decode(document, match.start())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(candidate, dict):
            yield candidate


def _is_remoting_config(candidate: dict[str, Any]) -> bool:
    return (
        isinstance(candidate.get("actions"), dict)
        and isinstance(candidate.get("service"), str)
        and isinstance(candidate.get("vf"), dict)
        and bool(candidate["vf"].get("vid"))
    )


def extract_remoting_config(document: str) -> dict[str, Any]:
    """Find the Salesforce Visualforce remoting configuration in a page."""
    documents = [document]
    unescaped = html.unescape(document)
    if unescaped != document:
        documents.append(unescaped)
    for candidate_document in documents:
        for candidate in _iter_json_objects(candidate_document):
            if _is_remoting_config(candidate):
                return candidate
    raise SapnError(
        "SAPN's remoting configuration was not found; the session expired "
        "or the portal changed."
    )


def response_message(response: Any) -> str | None:
    """Return a short error message from an SAPN remoting response."""
    if not isinstance(response, list) or not response or not isinstance(response[0], dict):
        return None
    first = response[0]
    for candidate in (first, first.get("result")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            for key in ("message", "errorMessage", "error", "status"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


class SapnPortal:
    """Authenticated session for the SAPN meter-data portal."""

    def __init__(
        self,
        email: str,
        password: str,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._timeout = timeout
        self._session = session or requests.Session()

    def close(self) -> None:
        self._session.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self._timeout)
        try:
            response = self._session.request(method, url, **kwargs)
            response.raise_for_status()
        except requests.RequestException as err:
            raise SapnError(f"SAPN request failed for {url}: {err}") from err
        return response

    def login(self) -> None:
        """Sign in. Raises SapnAuthError when SAPN rejects the credentials."""
        login_url = urljoin(BASE_URL, LOGIN_PAGE)
        page = self._request("GET", login_url)
        fields = hidden_inputs(page.text)
        view_state, view_state_mac = fields.get(VIEW_STATE), fields.get(VIEW_STATE_MAC)
        if not view_state or not view_state_mac:
            raise SapnError("SAPN's login form did not contain the Salesforce ViewState fields")
        response = self._request(
            "POST",
            login_url,
            data={
                FORM_PREFIX: FORM_PREFIX,
                f"{FORM_PREFIX}:username": self._email,
                f"{FORM_PREFIX}:password": self._password,
                f"{FORM_PREFIX}:loginButton": "Login",
                VIEW_STATE: view_state,
                VIEW_STATE_MAC: view_state_mac,
            },
        )
        redirect = extract_redirect(response.text)
        if redirect is not None:
            destination = self._request("GET", urljoin(response.url, redirect))
            if looks_like_login_page(destination.text):
                raise SapnAuthError("SAPN returned the login page after authentication")
        elif looks_like_login_page(response.text):
            raise SapnAuthError("SAPN rejected the login or asked for another verification step")
        _LOGGER.debug("SAPN login completed")

    def _remote_call(self, path: str, method: str, data: list[Any] | None = None) -> Any:
        page_url = urljoin(BASE_URL, path)
        page = self._request("GET", page_url)
        if looks_like_login_page(page.text):
            raise SapnAuthError("SAPN returned the login page; the session is not signed in")
        config = extract_remoting_config(page.text)
        action = metadata = None
        for action_name, action_data in config["actions"].items():
            if not isinstance(action_data, dict):
                continue
            for method_data in action_data.get("ms", []):
                if isinstance(method_data, dict) and method_data.get("name") == method:
                    action, metadata = action_name, method_data
        if action is None or metadata is None:
            raise SapnError(f"SAPN did not advertise {method!r} on {path}")
        body = {
            "action": action,
            "method": method,
            "type": "rpc",
            "tid": 1,
            "data": data,
            "ctx": {
                "csrf": metadata["csrf"],
                "vid": config["vf"]["vid"],
                "ns": metadata["ns"],
                "ver": metadata["ver"],
                "authorization": metadata["authorization"],
            },
        }
        response = self._request(
            "POST",
            urljoin(SERVICE_BASE, config["service"]),
            headers={"Content-Type": "application/json", "Referer": page_url},
            json=body,
        )
        try:
            return response.json()
        except ValueError as err:
            raise SapnError(f"SAPN returned a non-JSON response for {method!r}") from err

    def get_nmis(self) -> list[str]:
        """Return the NMIs assigned to the account."""
        data = self._remote_call("CADAccountPage", "getNMIAssignments")
        try:
            assignments = data[0]["result"]
            if not isinstance(assignments, list):
                raise TypeError
        except (IndexError, KeyError, TypeError) as err:
            raise SapnError("SAPN returned an unexpected NMI assignment response") from err
        nmis: list[str] = []
        for item in assignments:
            if not isinstance(item, dict):
                continue
            details = item.get("nmiAssign") if isinstance(item.get("nmiAssign"), dict) else {}
            nmi = str(item.get("theNMI") or details.get("NMI__c") or "").strip()
            if nmi:
                nmis.append(nmi)
        return nmis

    def download_nem12(self, nmi: str, start: date, end: date) -> str:
        """Return raw NEM12 text for an NMI and date range."""
        fmt = "%a, %d %b %Y %H:%M:%S GMT"
        request = [
            nmi,
            "SAPN",
            datetime.combine(start, time()).strftime(fmt),
            datetime.combine(end, time()).strftime(fmt),
            "Customer Access NEM12",
            "Detailed Report (CSV)",
            0,
        ]
        response = self._remote_call("CADRequestMeterData", "downloadNMIData", request)
        try:
            result = response[0]["result"]
        except (IndexError, KeyError, TypeError) as err:
            raise SapnError(f"SAPN returned an unexpected meter-data response for {nmi}") from err
        if not isinstance(result, dict) or "results" not in result:
            detail = response_message(response)
            raise SapnNoDataError(
                f"SAPN has no NEM12 data for {nmi} from {start} to {end}"
                + (f": {detail}" if detail else "")
            )
        text = result["results"]
        if not isinstance(text, str) or not text.strip():
            raise SapnNoDataError(f"SAPN returned an empty NEM12 file for {nmi}")
        return text


def login_and_list_nmis(email: str, password: str) -> list[str]:
    """Sign in and return the account's NMIs (for the config flow)."""
    portal = SapnPortal(email, password)
    try:
        portal.login()
        return portal.get_nmis()
    finally:
        portal.close()


def fetch_nem12(email: str, password: str, nmi: str, start: date, end: date) -> str:
    """Sign in and download NEM12 text for one NMI."""
    portal = SapnPortal(email, password)
    try:
        portal.login()
        return portal.download_nem12(nmi, start, end)
    finally:
        portal.close()
