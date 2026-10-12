"""
dom_interactor.py
Frame-aware DOM interaction and element inspection for Oracle APEX applications.
"""
import base64
import re
from html import unescape


def active_surfaces(page):
    """Returns all attached frames within the page."""
    return [frame for frame in page.frames if not frame.is_detached()]


def decode_html_text(raw_text: str) -> str:
    """Decodes escaped HTML entities and formats block text cleanly."""
    for _ in range(3):
        decoded = unescape(raw_text)
        if decoded == raw_text:
            break
        raw_text = decoded
    return re.sub(r'</?(?:br|p|div|span|b|strong|em|i|pre|code)(?:\s[^>]*)?\s*/?>', '\n', raw_text, flags=re.I).strip()


def find_visible_elements(page, css_selector: str):
    """Yields (frame, locator) tuples for all visible elements matching the selector across frames."""
    for frame in active_surfaces(page):
        try:
            loc = frame.locator(css_selector)
            for i in range(loc.count()):
                item = loc.nth(i)
                if item.is_visible():
                    yield frame, item
        except Exception:
            continue


def safe_click(locator, timeout: int = 4000) -> bool:
    """Attempts a safe click on a visible, enabled locator."""
    try:
        if not locator.is_visible() or not locator.is_enabled():
            return False
        locator.click(timeout=timeout)
        return True
    except Exception:
        return False


def locate_matching_button(page, text_patterns: list, modal_only: bool = False):
    """
    Finds a clickable button or anchor whose text or aria-label matches any of the regex patterns.
    Searches inside active modal dialogs first.
    """
    for frame in active_surfaces(page):
        try:
            if frame.is_detached():
                continue
            dialogs = frame.locator(".ui-dialog:visible, [role='dialog']:visible, .t-Dialog:visible")
            if modal_only:
                if not dialogs.count():
                    continue
                roots = [dialogs.nth(i) for i in range(dialogs.count())]
            else:
                roots = [dialogs.nth(i) for i in range(dialogs.count())] + [frame]

            for root in roots:
                controls = root.locator("button, a, input[type='button'], input[type='submit']")
                indices = controls.evaluate_all(r"""(elements, patterns) => {
                    const regexes = patterns.map(p => new RegExp(p, 'i'));
                    return elements.flatMap((el, i) => {
                        const text = el.querySelector('.t-Button-label')?.innerText
                            || (el.tagName === 'INPUT' ? el.value : el.innerText)
                            || el.getAttribute('aria-label') || '';
                        if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden'
                            || el.disabled || el.getAttribute('aria-disabled') === 'true') return [];
                        return regexes.some(re => re.test(text.trim().replace(/\s+/g, ' '))) ? [i] : [];
                    });
                }""", text_patterns)
                for index in indices:
                    el = controls.nth(index)
                    if el.is_visible() and el.is_enabled():
                        return el
        except Exception:
            continue
    return None


def click_button_by_patterns(page, text_patterns: list, modal_only: bool = False) -> bool:
    """Finds and clicks a button matching any of the given regex patterns."""
    btn = locate_matching_button(page, text_patterns, modal_only=modal_only)
    return btn is not None and safe_click(btn)


def extract_page_text(page) -> str:
    """Extracts all visible text from body across active frames."""
    texts = []
    for frame in active_surfaces(page):
        try:
            texts.append(frame.locator("body").inner_text(timeout=2000))
        except Exception:
            pass
    return "\n".join(texts)


def get_question_identity(page):
    """
    Extracts a unique, stable identity tuple for the currently displayed question:
    (stable_url, 'Question X of Y', question_text) or None.
    """
    for frame in active_surfaces(page):
        try:
            body = frame.locator("body").inner_text(timeout=2000)
            match = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", body, re.I)
            if match:
                regions = frame.locator('[region-id="question-Text"]:visible, [id*="question-Text"]:visible')
                text = regions.first.inner_text() if regions.count() else body
                stable_url = frame.url.replace(":RP:", "::")
                return (stable_url, match.group(0), text)
        except Exception:
            pass
    return None


def is_assessment_question_screen(page) -> bool:
    return get_question_identity(page) is not None


def is_assessment_landing_screen(page) -> bool:
    return not is_assessment_question_screen(page) and locate_matching_button(
        page, [r"^Take an Assessment$", r"^Finish Assessment$", r"^Resume Assessment$"]
    ) is not None


def is_assessment_start_dialog(page) -> bool:
    return locate_matching_button(page, [r"^Start$"], modal_only=True) is not None


def extract_question_data(page):
    """
    Extracts question text, optional diagram screenshot base64, choice label strings,
    and locator handles for all choices.
    """
    for frame in active_surfaces(page):
        try:
            if frame.is_detached():
                continue
            choices = frame.locator("button.choice-SelectArea:visible")
            if choices.count() < 2:
                choices = frame.locator('[role="radio"]:visible, [role="checkbox"]:visible')
            if choices.count() < 2:
                continue

            options, locators = [], []
            for i in range(choices.count()):
                choice = choices.nth(i)
                label = decode_html_text(choice.get_attribute("aria-label") or choice.inner_text())
                if label:
                    options.append(label)
                    locators.append(choice)

            regions = frame.locator('[region-id="question-Text"]:visible, [id*="question-Text"]:visible')
            image_b64 = None
            if regions.count():
                region = regions.first
                text = region.inner_text().strip()
                for img in region.locator("img:visible, svg:visible, canvas:visible").all():
                    box = img.bounding_box()
                    if box and box["width"] >= 40 and box["height"] >= 40:
                        image_b64 = base64.b64encode(region.screenshot()).decode("ascii")
                        break
            else:
                body = frame.locator("body").inner_text()
                match = re.search(r"Question\s+\d+\s+of\s+\d+([\s\S]+?)(?=Submit Answer|Next Question|$)", body, re.I)
                text = match.group(1).strip() if match else ""

            return text, image_b64, options, locators
        except Exception:
            continue
    return "", None, [], []


def get_wizard_steps(page):
    """Returns list of (frame, locator) for all wizard steps in the sidebar."""
    return list(find_visible_elements(page, "li.t-WizardSteps-step"))


def is_step_finished(step_locator) -> bool:
    """Checks if a sidebar step has been completed."""
    try:
        classes = (step_locator.get_attribute("class") or "").split()
        return (
            "is-complete" in classes
            or "is-complete-assessment" in classes
            or step_locator.locator(".fa-check, .fa-check-circle, .fa-check-circle-o, [class*='check']").count() > 0
        )
    except Exception:
        return False


def is_step_current(step_locator) -> bool:
    """Checks if the given sidebar step is currently active in the viewer."""
    try:
        state = step_locator.evaluate("""el => {
            const active = el.ownerDocument.querySelector('li.t-WizardSteps-step.is-active-module');
            if (active) return active === el;
            const markers = ['is-active', 'is-selected', 'is-current', 'active'];
            const link = el.querySelector('a, .t-WizardSteps-wrap');
            if (markers.some(c => el.classList.contains(c) || link?.classList.contains(c))) return true;
            if (link?.href === el.ownerDocument.location.href) return true;
            const marked = [...el.ownerDocument.querySelectorAll('li.t-WizardSteps-step')]
                .filter(item => [item, item.querySelector('a')].some(node =>
                    ['step', 'page', 'true'].includes(node?.getAttribute('aria-current'))));
            return marked.length === 1 && marked[0] === el;
        }""")
        return bool(state)
    except Exception:
        return False


def detect_multi_choice_mode(page, question_text: str, locators: list) -> bool:
    """Determines whether a question allows single-choice or multiple-choice answers."""
    for loc in locators:
        try:
            resp_type = loc.get_attribute("data-response-type")
            if resp_type == "1":
                return False
            if resp_type == "2":
                return True
            role = loc.get_attribute("role")
            if role == "radio":
                return False
            if role == "checkbox":
                return True
        except Exception:
            continue
    full_text = f"{question_text} {extract_page_text(page)}"
    if re.search(r"just one correct", full_text, re.I):
        return False
    return bool(re.search(r"select all that apply|choose (?:two|three|four|five|all|\d+)|which (?:two|three|four|five|\d+)", full_text, re.I))
