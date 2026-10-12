"""
engine.py
Autonomous course orchestrator and execution driver for Oracle Academy.
Traverses courses, navigates lessons, advances slides, and completes assessments.
"""
import re
import os
import sys
import json
import time
from playwright.sync_api import sync_playwright

from oracle_core.dom_interactor import (
    safe_click, click_button_by_patterns, locate_matching_button,
    is_assessment_question_screen, is_assessment_landing_screen, is_assessment_start_dialog,
    extract_question_data, find_visible_elements, extract_page_text, get_question_identity,
    get_wizard_steps, is_step_finished, is_step_current, detect_multi_choice_mode
)
from oracle_core.ai_solver import solve_with_session, validate_api
from oracle_core.session_keeper import keep_session_alive, wait_with_session, wait_until_ready, handle_native_dialog, SessionTimeoutError
from oracle_core.quiz_feedback import save_assessment_results
from oracle_core.logger import log_info, log_ok, log_warn, log_err, save_diagnostic_snapshot
from oracle_core.backoff_policy import BackoffBudget, get_browser_state

STEP_DELAY = 2.0
PAGE_LOAD_TIMEOUT = 45000
ACADEMY_HOME_URL = "https://academy.oracle.com/pls/f?p=63000:100"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSER_PROFILE_DIR = os.path.join(PROJECT_ROOT, "chrome_session")

RETURN_CONTROLS = [r"^Return to Course Lessons$", r"^Return to Course$", r"^Course Outline$", r"^Exit$", r"^Course$"]
SCORE_SUMMARY_REGEX = r"Results Summary|Assessment Score|Assessment (?:is )?Complete|Score Summary|Percentage Scored|Mastery Score"

LAST_ERROR_REASON = ""


def fail_step(page, reason: str) -> bool:
    global LAST_ERROR_REASON
    LAST_ERROR_REASON = reason
    save_diagnostic_snapshot(page, reason)
    log_err(reason)
    return False


def get_page_fingerprint(page):
    frames_data = []
    for frame in page.frames:
        try:
            steps = frame.locator('li.t-WizardSteps-step')
            status = tuple((item.get_attribute('class'), is_step_finished(item)) for item in steps.all())
            frames_data.append((frame.url, frame.locator('body').inner_text(timeout=2000), status))
        except Exception:
            continue
    return page.url, tuple(frames_data)


def finish_assessment_flow(page):
    keep_session_alive(page)
    if not re.search(SCORE_SUMMARY_REGEX, extract_page_text(page), re.I):
        if not click_button_by_patterns(page, [r"^Complete Assessment$", r"^Submit Assessment$"]):
            return fail_step(page, "Assessment results not visible and submission button not found.")
        wait_with_session(page, 0.5)
        click_button_by_patterns(page, [r"^Complete Assessment$", r"^Submit Assessment$", r"^Yes$", r"^Confirm$"], modal_only=True)
        if not wait_until_ready(page, lambda: bool(re.search(SCORE_SUMMARY_REGEX, extract_page_text(page), re.I))):
            return fail_step(page, "Assessment submission did not transition to results screen.")

    score = save_assessment_results(page)

    for attempt in range(3):
        if locate_matching_button(page, RETURN_CONTROLS) is not None:
            break
        if attempt:
            log_info(f"Results loading; retrying View Results ({attempt + 1}/3)...")
        click_button_by_patterns(page, [r"^View Results$"])
        if wait_until_ready(page, lambda: locate_matching_button(page, RETURN_CONTROLS) is not None, timeout=15):
            break
    else:
        return fail_step(page, "Could not locate Return to Course control after viewing results.")

    final_score = save_assessment_results(page)
    score = final_score if final_score is not None else score

    if not click_button_by_patterns(page, RETURN_CONTROLS):
        return fail_step(page, "Failed to click Return to Course button.")

    if not wait_until_ready(page, lambda: bool(get_wizard_steps(page) or get_section_links(page) or get_course_buttons(page))):
        return fail_step(page, "Return action did not exit the assessment view.")

    if score is not None and score < 70:
        retry_info = getattr(page, '_quiz_retry_state', None)
        if not retry_info:
            return fail_step(page, f"Assessment scored {score}% (< 70%). Feedback saved.")
        if retry_info['retries'] >= 2:
            return fail_step(page, f"Assessment scored {score}% (< 70%) after maximum retries.")
        retry_info['retries'] += 1
        log_info(f"Assessment score {score}% is below 70%. Retrying assessment ({retry_info['retries']}/2)...")
        page.goto(retry_info['url'], wait_until='domcontentloaded', timeout=PAGE_LOAD_TIMEOUT)
        if not wait_until_ready(page, lambda: is_assessment_landing_screen(page)):
            return fail_step(page, "Could not reload assessment for retry.")
        return start_and_solve_assessment(page, retry=True)

    if hasattr(page, '_quiz_retry_state'):
        del page._quiz_retry_state

    log_ok("Assessment submitted successfully. Score verified.")
    return True


def check_selection_state(locators: list, selected_indices: list) -> bool:
    for i, loc in enumerate(locators):
        val = loc.get_attribute("aria-checked", timeout=1000)
        if val is not None and (val == "true") != (i in selected_indices):
            return False
    return True


def is_lesson_viewer_ready(page) -> bool:
    if locate_matching_button(page, [r"^Save and Continue$", r"^Save Progress$"]) is not None:
        return True
    if is_assessment_landing_screen(page) or is_assessment_question_screen(page) or is_assessment_start_dialog(page):
        return True
    steps = get_wizard_steps(page)
    return bool(steps and all(is_step_finished(item) for _, item in steps))


def solve_current_assessment(page):
    seen_questions = set()
    for _ in range(200):
        keep_session_alive(page)
        identity = get_question_identity(page)
        if identity is None:
            return finish_assessment_flow(page)
        if identity in seen_questions:
            return fail_step(page, "Question did not advance; halting to avoid repeating answer.")

        q_text, img_b64, opts, locs = extract_question_data(page)
        if not q_text or len(opts) < 2:
            return fail_step(page, "Could not extract question text and options.")

        is_multiple = detect_multi_choice_mode(page, q_text, locs)
        log_info(f"Solving {identity[1]} ({len(opts)} options, multi_choice={is_multiple})")

        try:
            chosen_indices = solve_with_session(page, q_text, opts, img_b64, is_multiple)
        except SessionTimeoutError:
            raise
        except Exception as exc:
            return fail_step(page, f"AI solver failed: {exc}")

        keep_session_alive(page)
        if get_question_identity(page) != identity:
            return fail_step(page, "Question changed during AI evaluation.")

        _, _, fresh_opts, locs = extract_question_data(page)
        if opts != fresh_opts:
            return fail_step(page, "Options changed during evaluation.")

        if not chosen_indices or any(not isinstance(i, int) or i < 0 or i >= len(locs) for i in chosen_indices):
            return fail_step(page, "Invalid choice indices returned by solver.")

        if not is_multiple and len(chosen_indices) > 1:
            chosen_indices = chosen_indices[:1]

        for i, loc in enumerate(locs):
            is_checked = loc.get_attribute("aria-checked") == "true"
            if (i in chosen_indices and not is_checked) or (is_multiple and i not in chosen_indices and is_checked):
                if not safe_click(loc):
                    return fail_step(page, f"Failed to click option {i}.")

        if not wait_until_ready(page, lambda: get_question_identity(page) == identity and check_selection_state(locs, chosen_indices), timeout=10):
            return fail_step(page, "Option selection did not settle within timeout.")

        seen_questions.add(identity)
        wait_with_session(page, 0.5)

        if not click_button_by_patterns(page, [r"^Submit Answer$", r"^Next Question$", r"^Next$"]):
            return finish_assessment_flow(page)

        if not wait_until_ready(page, lambda: get_question_identity(page) != identity or locate_matching_button(page, [r"^Next Question$", r"^Next$"]) is not None):
            return fail_step(page, "Submit action did not advance.")

        if get_question_identity(page) == identity:
            if not click_button_by_patterns(page, [r"^Next Question$", r"^Next$"]) or not wait_until_ready(page, lambda: get_question_identity(page) != identity):
                return fail_step(page, "Failed to advance past feedback screen.")

    return fail_step(page, "Assessment exceeded 200 question safety limit.")


def start_and_solve_assessment(page, retry: bool = False):
    keep_session_alive(page)
    if not is_assessment_start_dialog(page):
        retry_info = getattr(page, '_quiz_retry_state', None)
        if not retry and (retry_info is None or retry_info['url'] != page.url):
            page._quiz_retry_state = {'url': page.url, 'retries': 0}
        if not click_button_by_patterns(page, [r"^Take an Assessment$", r"^Finish Assessment$", r"^Resume Assessment$"]):
            return fail_step(page, "Could not open assessment.")
        if not wait_until_ready(page, lambda: is_assessment_start_dialog(page) or is_assessment_question_screen(page)):
            return fail_step(page, "Assessment did not show Start dialog or question.")

    if is_assessment_start_dialog(page):
        if not click_button_by_patterns(page, [r"^Start$"], modal_only=True):
            return fail_step(page, "Failed to click Start in dialog.")
        if not wait_until_ready(page, lambda: is_assessment_question_screen(page)):
            return fail_step(page, "Start action did not open question screen.")

    return solve_current_assessment(page)


def process_section_lessons(page):
    for _ in range(150):
        wait_with_session(page, STEP_DELAY)
        steps = get_wizard_steps(page)
        if steps and all(is_step_finished(item) for _, item in steps):
            log_ok(f"All {len(steps)} sidebar steps completed in this section.")
            return True

        if is_assessment_landing_screen(page) and any(is_step_current(item) and is_step_finished(item) for _, item in steps):
            remaining = [item for _, item in steps if not is_step_finished(item)]
            if remaining:
                fingerprint = get_page_fingerprint(page)
                target = remaining[0]
                link = target.locator("a.t-WizardSteps-wrap, a, .t-WizardSteps-label").first
                log_info("Completed assessment detected; jumping to next incomplete item...")
                if not (safe_click(link) or safe_click(target)):
                    return fail_step(page, "Could not navigate away from completed assessment.")
                if not wait_until_ready(page, lambda: get_page_fingerprint(page) != fingerprint):
                    return fail_step(page, "Navigation made no progress.")
                continue

        if is_assessment_start_dialog(page) or is_assessment_landing_screen(page):
            if not start_and_solve_assessment(page):
                return False
            continue

        if is_assessment_question_screen(page):
            if not solve_current_assessment(page):
                return False
            continue

        if re.search(SCORE_SUMMARY_REGEX, extract_page_text(page), re.I):
            if not finish_assessment_flow(page):
                return False
            continue

        fingerprint = get_page_fingerprint(page)
        incomplete = [item for _, item in steps if not is_step_finished(item)]

        if incomplete and is_step_current(incomplete[0]) and "is-incomplete-assessment" not in (incomplete[0].get_attribute("class") or "") and not is_lesson_viewer_ready(page):
            log_info("Waiting for lesson viewer controls to load (up to 30s)...")
            if wait_until_ready(page, lambda: is_lesson_viewer_ready(page), timeout=30):
                continue
            return fail_step(page, "Lesson viewer controls timed out.")

        if incomplete:
            target = incomplete[0]
            if not is_step_current(target):
                link = target.locator("a.t-WizardSteps-wrap, a, .t-WizardSteps-label").first
                log_info(f"Opening incomplete step ({len(incomplete)} remaining)...")
                try:
                    link.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                if not (safe_click(link) or safe_click(target)):
                    return fail_step(page, "Failed to click incomplete step.")
                if not wait_until_ready(page, lambda: get_page_fingerprint(page) != fingerprint):
                    return fail_step(page, "Step click made no progress.")
                continue

        if incomplete and "is-incomplete-assessment" in (incomplete[0].get_attribute("class") or ""):
            if wait_until_ready(page, lambda: is_assessment_landing_screen(page) or is_assessment_question_screen(page) or is_assessment_start_dialog(page)):
                continue
            return fail_step(page, "Incomplete assessment opened without usable controls.")

        save_btn = locate_matching_button(page, [r"^Save and Continue$", r"^Save Progress$"])
        if save_btn is not None:
            safe_click(save_btn)
            if not wait_until_ready(page, lambda: get_page_fingerprint(page) != fingerprint, timeout=30):
                return fail_step(page, "Save and Continue did not advance.")
            log_info("Lesson saved; advanced to next item.")
            continue

        if incomplete:
            link = incomplete[0].locator("a.t-WizardSteps-wrap, a").first
            if not safe_click(link) or not wait_until_ready(page, lambda: get_page_fingerprint(page) != fingerprint):
                return fail_step(page, "Failed to open incomplete lesson.")
            continue

        if list(find_visible_elements(page, "li.t-MediaList-item a.t-MediaList-itemWrap")):
            return True

        return fail_step(page, "Unrecognized course outline state.")
    return fail_step(page, "Section exceeded 150 action limit.")


def get_course_buttons(page):
    controls = []
    for frame, item in find_visible_elements(page, "button, a"):
        if re.fullmatch(r"Take Class", item.inner_text().strip(), re.I):
            controls.append((frame, item))
    return controls


def get_section_links(page):
    return [
        (frame, item)
        for frame, item in find_visible_elements(page, "li.t-MediaList-item a.t-MediaList-itemWrap")
        if (item.get_attribute("href") or "").strip() not in ("", "#")
    ]


def get_element_display_label(item) -> str:
    try:
        title = item.locator('.t-MediaList-title')
        if title.count():
            return title.first.inner_text().strip()
        card = item.locator('xpath=ancestor::*[contains(@class, "a-CardView-item") or contains(@class, "t-Card") or self::li][1]')
        if card.count():
            card_title = card.locator('.a-CardView-title, .t-Card-title, h3, h2').first
            if card_title.count():
                return card_title.inner_text().strip()
        return item.inner_text().strip()
    except Exception:
        return ""


def get_frame_path(frame):
    indices = []
    while frame.parent_frame is not None:
        parent = frame.parent_frame
        indices.append([child for child in parent.child_frames if not child.is_detached()].index(frame))
        frame = parent
    return list(reversed(indices))


def resolve_frame_by_path(page, indices: list):
    frame = page.main_frame
    for idx in indices:
        children = [child for child in frame.child_frames if not child.is_detached()]
        if idx >= len(children):
            return None
        frame = children[idx]
    return frame


def traverse_course_hierarchy(page, depth: int = 0) -> bool:
    if depth > 4:
        return fail_step(page, "Navigation hierarchy exceeded 4 levels.")
    keep_session_alive(page)

    classes = get_course_buttons(page)
    sections = get_section_links(page) if not classes else []
    items = classes or sections

    if not items:
        return process_section_lessons(page)

    entity_type = "course" if classes else "section"
    labels = [get_element_display_label(item) for _, item in items]
    parent_url = page.url
    parent_frame = items[0][0]
    parent_frame_path = get_frame_path(parent_frame)
    parent_frame_url = parent_frame.url
    is_main_frame = (parent_frame == page.main_frame)

    for index, label in enumerate(labels):
        keep_session_alive(page)
        wait_until_ready(page, lambda: len(get_course_buttons(page) if classes else get_section_links(page)) == len(labels), timeout=10)
        current = get_course_buttons(page) if classes else get_section_links(page)

        if len(current) != len(labels) or get_element_display_label(current[index][1]) != label:
            return fail_step(page, f"List modified during run; cannot safely identify {entity_type} {index + 1}.")

        # Check completion percentage badge: only skip if EXPLICITLY 100%!
        # Unopened sections with no badge or 0% are actively visited.
        if not classes:
            try:
                badge = current[index][1].locator('.t-MediaList-badge, [class*="badge"]')
                if not badge.count():
                    badge = current[index][1].locator('xpath=ancestor::li[1]').locator('.t-MediaList-badge, [class*="badge"]')
                badge_text = badge.first.inner_text() if badge.count() else ""
                pct_match = re.search(r'(\d+)\s*%', badge_text)
                if pct_match and int(pct_match.group(1)) == 100:
                    log_ok(f"Skipping {label} (already 100% complete).")
                    continue
            except Exception:
                pass

        fingerprint = get_page_fingerprint(page)
        log_info(f"Opening {entity_type} {index + 1}/{len(labels)}: {label}")

        if not safe_click(current[index][1]) or not wait_until_ready(page, lambda: get_page_fingerprint(page) != fingerprint):
            return fail_step(page, f"Opening {entity_type} {index + 1} made no progress.")

        wait_with_session(page, STEP_DELAY)

        if not traverse_course_hierarchy(page, depth + 1):
            return False

        if is_main_frame:
            page.goto(parent_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        else:
            page.goto("about:blank")
            page.goto(parent_url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT)
            wait_with_session(page, 0.25)
            wait_until_ready(page, lambda: resolve_frame_by_path(page, parent_frame_path) is not None)
            frame = resolve_frame_by_path(page, parent_frame_path)
            if frame is None:
                return fail_step(page, "Parent frame detached unexpectedly.")
            frame.goto(parent_frame_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)

        wait_with_session(page, STEP_DELAY)

    log_ok(f"All {len(labels)} {entity_type} entries completed.")
    return True


def execute_full_automation(page) -> bool:
    """Executes full course sweep across all enrolled courses."""
    keep_session_alive(page)
    if not get_course_buttons(page):
        if not get_section_links(page) and not process_section_lessons(page):
            return False
        page.goto(ACADEMY_HOME_URL, wait_until='domcontentloaded', timeout=PAGE_LOAD_TIMEOUT)
        if not wait_until_ready(page, lambda: bool(get_course_buttons(page))):
            return fail_step(page, "Could not reach Classes dashboard.")
    return traverse_course_hierarchy(page)


def start_oracle_automation():
    """Main lifecycle entrypoint for the automation engine."""
    global LAST_ERROR_REASON
    validate_api()

    with sync_playwright() as p:
        browser_conn = None
        page = None

        try:
            browser_conn = p.chromium.connect_over_cdp("http://localhost:9222", timeout=1500)
            for ctx in browser_conn.contexts:
                for pg in ctx.pages:
                    if "63000:" in pg.url:
                        page = pg
                        break
            if not page and browser_conn.contexts:
                page = browser_conn.contexts[0].pages[0] if browser_conn.contexts[0].pages else browser_conn.contexts[0].new_page()
            log_info(f"Attached to active Chrome on port 9222 ({page.url[:60]})")
        except Exception:
            browser_conn = None

        if not browser_conn:
            os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
            log_info("Launching Chrome with student profile...")
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    headless=False,
                    channel="chrome",
                    no_viewport=True,
                    args=["--start-maximized"]
                )
            except Exception as exc:
                if "profile is already in use" in str(exc).lower() or "existing browser session" in str(exc).lower():
                    log_warn("A Chrome window is already using this profile.")
                    input("Close existing Chrome windows, then press Enter to continue: ")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(BROWSER_PROFILE_DIR),
                        headless=False,
                        channel="chrome",
                        no_viewport=True,
                        args=["--start-maximized"]
                    )
                else:
                    raise

            for extra in list(context.pages)[1:]:
                try:
                    if extra.url in ("about:blank", "chrome://newtab/", ""):
                        extra.close()
                except Exception:
                    pass
            page = context.pages[0] if context.pages else context.new_page()

        page.bring_to_front()
        page.set_default_timeout(10000)
        page.on("dialog", handle_native_dialog)

        if "63000:" not in page.url:
            page.goto(ACADEMY_HOME_URL, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

        print("\n" + "=" * 70)
        print("  Oracle Academy Automation Engine")
        print("  1. Please log into Oracle Academy in Chrome if prompted.")
        print("  2. Open 'My Classes' or your course outline.")
        print("  Press Enter here to begin full automation...")
        print("=" * 70 + "\n")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        budget = BackoffBudget()
        while True:
            LAST_ERROR_REASON = ""
            try:
                if execute_full_automation(page):
                    log_ok("All courses and sections completed successfully!")
                    print("\nPress Enter to exit...")
                    try:
                        input()
                    except Exception:
                        pass
                    break
            except SessionTimeoutError as exc:
                LAST_ERROR_REASON = str(exc)
                save_diagnostic_snapshot(page, str(exc))
                log_err(str(exc))
            except Exception as exc:
                LAST_ERROR_REASON = str(exc)
                save_diagnostic_snapshot(page, f"Execution error: {exc}")
                log_err(str(exc))

            try:
                delay = budget.next_delay(LAST_ERROR_REASON, get_browser_state(page))
                if delay is not None:
                    log_info(f"Page is loading slowly. Retrying automatically in {delay}s...")
                    wait_with_session(page, delay)
                    continue
            except SessionTimeoutError:
                log_warn("Oracle session expired. Please sign in again.")
            except Exception as exc:
                log_warn(f"State check skipped: {exc}")

            ans = input("\nAutomation paused. Fix browser state and press Enter to retry (q to quit): ")
            if ans.strip().lower() == "q":
                break
