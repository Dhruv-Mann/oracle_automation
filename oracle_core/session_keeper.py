"""
session_keeper.py
Oracle APEX session timeout prevention, auto-extension, and cooperative waiting.
"""
import re
import time
from playwright.sync_api import Error as PlaywrightError
from oracle_core.dom_interactor import active_surfaces, extract_page_text, safe_click
from oracle_core.logger import log_info


class SessionTimeoutError(RuntimeError):
    pass


def is_transient_browser_error(exc) -> bool:
    return isinstance(exc, PlaywrightError) and bool(re.search(
        r'frame was detached|execution context was destroyed|cannot find context|navigation|not attached', str(exc), re.I))


def keep_session_alive(page) -> bool:
    """
    Cooperatively checks for Oracle APEX session inactivity warnings across all frames.
    If an extension button is visible, automatically clicks it to extend session.
    Raises SessionTimeoutError if the session has fully expired or returned to login.
    """
    for frame in active_surfaces(page):
        try:
            if frame.is_detached():
                continue
            dialogs = frame.locator(".ui-dialog:visible, [role='dialog']:visible, .t-Dialog:visible, .a-SessionTimeout:visible")
            for dialog in dialogs.element_handles():
                if not dialog.is_visible():
                    continue
                text = dialog.inner_text()
                if not re.search(r"session|inactivity|inactive|logged out", text, re.I):
                    continue
                if re.search(r"session (?:has )?expired|session (?:has )?ended|logged out", text, re.I):
                    raise SessionTimeoutError("Oracle session expired. Please sign in again in the browser.")
                controls = dialog.query_selector_all("button, a, input[type='button'], input[type='submit']")
                for el in controls:
                    label = (el.get_attribute("value") or el.inner_text()).strip()
                    if re.fullmatch(r"(?:Extend(?: Session)?|Stay (?:Signed|Logged) In|Continue(?: Session)?|Keep (?:Session )?Alive|Yes|OK)", label, re.I):
                        if safe_click(el):
                            log_info("Extended Oracle session from the timeout warning.")
                            return True
                        if not dialog.is_visible():
                            return False
                        raise RuntimeError("Session extension button could not be clicked.")
                if not dialog.is_visible():
                    continue
                raise RuntimeError("Session warning appeared, but its extension button was not recognized.")
        except SessionTimeoutError:
            raise
        except PlaywrightError as exc:
            if not is_transient_browser_error(exc):
                raise

    text = extract_page_text(page)
    if re.search(r"signon\.oracle\.com/(?:signin|login)|login\.oracle\.com", page.url, re.I):
        raise SessionTimeoutError("Oracle redirected to sign-in service.")
    if re.search(r"(?:your )?session (?:has )?(?:expired|ended)", text, re.I):
        raise SessionTimeoutError("Oracle session expired. Please sign in again in the browser.")

    for frame in active_surfaces(page):
        try:
            if not frame.is_detached() and frame.locator('input[type="password"]:visible').count():
                raise SessionTimeoutError("Oracle returned to the login page.")
        except SessionTimeoutError:
            raise
        except PlaywrightError as exc:
            if not is_transient_browser_error(exc):
                raise
    return False


def wait_with_session(page, seconds: float):
    """Waits for the given duration while maintaining Oracle session periodically."""
    deadline = time.monotonic() + seconds
    while True:
        keep_session_alive(page)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        page.wait_for_timeout(min(500, remaining * 1000))


def wait_until_ready(page, condition_fn, timeout: int = 15) -> bool:
    """Polls predicate while keeping the session alive until condition passes or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        keep_session_alive(page)
        try:
            if condition_fn():
                return True
        except PlaywrightError as exc:
            if not is_transient_browser_error(exc):
                raise
        page.wait_for_timeout(250)
    return False


def handle_native_dialog(dialog):
    """Auto-confirms native session extension and assessment completion alerts."""
    msg = dialog.message.lower()
    if dialog.type == "confirm" and "session" in msg and any(w in msg for w in ("extend", "continue", "remain", "stay")):
        dialog.accept()
        log_info("Extended Oracle session from native browser confirmation.")
    elif dialog.type == "confirm" and "assessment" in msg and any(w in msg for w in ("submit", "complete", "finish")):
        dialog.accept()
    else:
        dialog.dismiss()
