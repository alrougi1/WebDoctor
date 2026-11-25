"""Overlay dismissal must not click arbitrary form submit buttons."""
from webdoctor.checks.browser import OVERLAY_DISMISS_SELECTORS, _dismiss_overlays


class _FakeElement:
    def __init__(self, is_submit=False):
        self.is_submit = is_submit
        self.clicked = False

    def is_visible(self, timeout=0):
        return True

    def evaluate(self, _script):
        return self.is_submit

    def click(self, timeout=0):
        self.clicked = True


class _FakePage:
    def __init__(self, is_submit):
        self.is_submit = is_submit
        self.elements = []
        self.locators = []

    def locator(self, selector):
        self.locators.append(selector)
        element = _FakeElement(self.is_submit)
        self.elements.append(element)
        return type("First", (), {"first": element})()

    def wait_for_timeout(self, _ms):
        pass


def test_accept_terms_submit_button_is_not_clicked():
    page = _FakePage(is_submit=True)
    assert _dismiss_overlays(page) is False
    assert not any(el.clicked for el in page.elements)
    assert not any("button:has-text('Accept')" == s for s in page.locators), (
        "unscoped Accept selector must not be used"
    )


def test_scoped_consent_button_is_clicked():
    page = _FakePage(is_submit=False)
    assert _dismiss_overlays(page) is True
    assert page.elements[0].clicked
    assert page.elements[0] is not None
    assert all("button:has-text" in s or "close" in s.lower() or "accept" in s.lower()
               for s in OVERLAY_DISMISS_SELECTORS)
