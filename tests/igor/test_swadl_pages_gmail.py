"""tests/test_swadl_pages_gmail.py — unit tests for Gmail page objects.

Tests use mock WebElements and a mock selenium driver — no browser required.
The mock driver is injected via driver_override so SWADL's global cfgdict
driver is never touched.

Integration tests (real browser + throwaway Gmail account) are in the
separate T-gmail-flow-integration-test ticket, gated on account availability.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path.home() / "TheIgors"))

from wild_igor.tools.swadl_pages.gmail_compose import ComposePage
from wild_igor.tools.swadl_pages.gmail_inbox import InboxPage
from wild_igor.tools.swadl_pages.gmail_message_ref import MessageRef

# ── helpers ───────────────────────────────────────────────────────────────


def _mock_row(thread_id: str, subject: str, from_addr: str, snippet: str) -> MagicMock:
    """Build a mock selenium WebElement representing one inbox row."""
    row = MagicMock()
    row.get_attribute.side_effect = lambda attr: (
        thread_id if attr == "data-legacy-thread-id" else None
    )

    subj_el = MagicMock()
    subj_el.text = subject

    from_el = MagicMock()
    from_el.get_attribute.return_value = from_addr
    from_el.text = from_addr

    snip_el = MagicMock()
    snip_el.text = snippet

    def _find_element(by, sel):
        if "bog" in sel:
            return subj_el
        if sel == "[email]":
            return from_el
        if ".y2" in sel:
            return snip_el
        raise Exception(f"no mock for selector {sel!r}")

    row.find_element.side_effect = _find_element
    return row


def _mock_driver(rows: list | None = None, current_url: str = "") -> MagicMock:
    driver = MagicMock()
    driver.current_url = current_url
    if rows is not None:
        driver.find_elements.return_value = rows
    return driver


# ── MessageRef ────────────────────────────────────────────────────────────


class TestMessageRef:
    def test_fields_accessible(self):
        ref = MessageRef(id="t1", subject="Hello", from_addr="a@b.com", snippet="snip")
        assert ref.id == "t1"
        assert ref.subject == "Hello"
        assert ref.from_addr == "a@b.com"
        assert ref.snippet == "snip"

    def test_as_summary_returns_message_summary(self):
        from wild_igor.tools.swadl_flows.gmail import MessageSummary

        ref = MessageRef(id="t1", subject="s", from_addr="f@x", snippet="snip")
        s = ref.as_summary()
        assert isinstance(s, MessageSummary)
        assert s.id == "t1"
        assert s.subject == "s"

    def test_archive_clicks_row_then_archive_button(self):
        row_el = MagicMock()
        archive_btn = MagicMock()
        driver = MagicMock()
        driver.find_element.return_value = archive_btn

        ref = MessageRef(
            id="t1",
            subject="s",
            from_addr="f",
            snippet="snip",
            _row_element=row_el,
            _driver=driver,
        )
        ref.archive()

        row_el.click.assert_called_once()
        driver.find_element.assert_called_once()
        archive_btn.click.assert_called_once()

    def test_archive_no_element_no_crash(self):
        ref = MessageRef(id="t1", subject="s", from_addr="f", snippet="snip")
        ref.archive()  # should not raise

    def test_equality_ignores_private_fields(self):
        a = MessageRef(
            id="t1",
            subject="s",
            from_addr="f",
            snippet="snip",
            _row_element=MagicMock(),
            _driver=MagicMock(),
        )
        b = MessageRef(id="t1", subject="s", from_addr="f", snippet="snip")
        assert a == b


# ── InboxPage ─────────────────────────────────────────────────────────────


class TestInboxPageLoad:
    def _set_profile(self, value):
        import SWADL.engine.swadl_cfg as cfg_mod
        import SWADL.engine.swadl_constants as const_mod

        if value is None:
            cfg_mod.cfgdict.pop(const_mod.SELENIUM_USER_DATA_DIR, None)
        else:
            cfg_mod.cfgdict[const_mod.SELENIUM_USER_DATA_DIR] = value

    def test_load_navigates_to_inbox_url(self):
        self._set_profile("/fake/profile")
        try:
            driver = _mock_driver()
            InboxPage(driver_override=driver).load()
            driver.get.assert_called_once_with(
                "https://mail.google.com/mail/u/0/#inbox"
            )
        finally:
            self._set_profile(None)

    def test_load_raises_when_profile_not_set(self):
        self._set_profile(None)
        driver = _mock_driver()
        with pytest.raises(RuntimeError, match="SELENIUM_USER_DATA_DIR"):
            InboxPage(driver_override=driver).load()


class TestInboxPageFirstNMessages:
    def test_returns_n_refs(self):
        rows = [
            _mock_row(f"t{i}", f"subj{i}", f"s{i}@x.com", f"snip{i}") for i in range(5)
        ]
        driver = _mock_driver(rows=rows)
        page = InboxPage(driver_override=driver)
        result = page.first_n_messages(3)
        assert len(result) == 3

    def test_ref_fields_populated(self):
        rows = [_mock_row("tid-42", "My Subject", "sender@example.com", "preview text")]
        driver = _mock_driver(rows=rows)
        page = InboxPage(driver_override=driver)
        refs = page.first_n_messages(1)
        assert refs[0].id == "tid-42"
        assert refs[0].subject == "My Subject"
        assert refs[0].from_addr == "sender@example.com"
        assert refs[0].snippet == "preview text"

    def test_n_zero_returns_empty(self):
        driver = _mock_driver(rows=[_mock_row("t1", "s", "f", "snip")])
        result = InboxPage(driver_override=driver).first_n_messages(0)
        assert result == []

    def test_n_larger_than_rows_returns_all(self):
        rows = [_mock_row(f"t{i}", "s", "f", "snip") for i in range(2)]
        driver = _mock_driver(rows=rows)
        result = InboxPage(driver_override=driver).first_n_messages(10)
        assert len(result) == 2


class TestInboxPageFindMessage:
    def test_finds_matching_thread_id(self):
        rows = [
            _mock_row("t1", "first", "a@x", "snip1"),
            _mock_row("t2", "second", "b@x", "snip2"),
        ]
        driver = _mock_driver(rows=rows)
        ref = InboxPage(driver_override=driver).find_message("t2")
        assert ref is not None
        assert ref.id == "t2"
        assert ref.subject == "second"

    def test_returns_none_when_not_found(self):
        rows = [_mock_row("t1", "s", "f", "snip")]
        driver = _mock_driver(rows=rows)
        ref = InboxPage(driver_override=driver).find_message("missing")
        assert ref is None

    def test_empty_inbox_returns_none(self):
        driver = _mock_driver(rows=[])
        assert InboxPage(driver_override=driver).find_message("any") is None


class TestInboxPageOpenCompose:
    def test_clicks_compose_button_and_returns_compose_page(self):
        compose_btn = MagicMock()
        driver = MagicMock()
        driver.find_element.return_value = compose_btn

        page = InboxPage(driver_override=driver)
        compose = page.open_compose()

        compose_btn.click.assert_called_once()
        assert isinstance(compose, ComposePage)

    def test_compose_page_inherits_driver(self):
        driver = MagicMock()
        driver.find_element.return_value = MagicMock()
        compose = InboxPage(driver_override=driver).open_compose()
        assert compose._driver_override is driver


# ── ComposePage ───────────────────────────────────────────────────────────


class TestComposePageFields:
    def setup_method(self):
        self.to_el = MagicMock()
        self.subj_el = MagicMock()
        self.body_el = MagicMock()
        self.send_el = MagicMock()

        driver = MagicMock()
        driver.current_url = "https://mail.google.com/mail/u/0/#sent/thread-abc123"

        def _find(by, sel):
            if 'To"' in sel:
                return self.to_el
            if 'Subject"' in sel:
                return self.subj_el
            if 'Body"' in sel:
                return self.body_el
            if "Send" in sel:
                return self.send_el
            raise Exception(f"unmocked selector: {sel!r}")

        driver.find_element.side_effect = _find
        self.driver = driver
        self.page = ComposePage(driver_override=driver)

    def test_set_to_sends_keys(self):
        self.page.set_to("to@example.com")
        self.to_el.send_keys.assert_called_once_with("to@example.com")

    def test_set_subject_sends_keys(self):
        self.page.set_subject("Hello")
        self.subj_el.send_keys.assert_called_once_with("Hello")

    def test_set_body_sends_keys(self):
        self.page.set_body("body text")
        self.body_el.send_keys.assert_called_once_with("body text")

    def test_send_clicks_send_button(self):
        self.page.send()
        self.send_el.click.assert_called_once()

    def test_send_returns_thread_id_from_url(self):
        result = self.page.send()
        assert result == "thread-abc123"


class TestComposePageExtractSentId:
    def _page(self, url: str) -> ComposePage:
        driver = MagicMock()
        driver.current_url = url
        driver.find_element.return_value = MagicMock()
        return ComposePage(driver_override=driver)

    def test_extracts_id_from_sent_fragment(self):
        assert (
            self._page("https://mail.google.com/#sent/abc123")._extract_sent_id()
            == "abc123"
        )

    def test_no_sent_marker_returns_empty(self):
        assert self._page("https://mail.google.com/#inbox")._extract_sent_id() == ""

    def test_sent_without_id_returns_empty(self):
        assert self._page("https://mail.google.com/#sent")._extract_sent_id() == ""

    def test_empty_url_returns_empty(self):
        assert self._page("")._extract_sent_id() == ""
