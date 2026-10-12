"""
backoff_policy.py
Adaptive backoff budget and page state tracking for temporary Oracle delays.
"""
import re


class BackoffBudget:
    delays = (5, 15, 30, 60, 60, 60, 60, 60)

    def __init__(self):
        self.counts = {}

    def next_delay(self, reason: str, state_fingerprint: str):
        """
        Determines the appropriate retry delay based on failure reason and current page state.
        Returns seconds to wait (int) or None if error is unrecoverable.
        """
        if re.search(r'Gemini|Gemma|API limit|below (?:the required )?70%|question changed|Question did not advance|Invalid answer|session expired|sign-in', reason, re.I):
            return None

        categories = (
            ('lesson', r'lesson controls|Incomplete sidebar lesson|Save action|confirm saving|Save and Continue control'),
            ('selection', r'selection state|Answer selection'),
            ('results', r'View Results|results-navigation waits|Return to Course did not leave'),
            ('navigation', r'TimeoutError|frame was detached|execution context was destroyed'),
        )
        category = next((name for name, pattern in categories if re.search(pattern, reason, re.I)), None)
        if category is None:
            return None

        key = (category, state_fingerprint)
        count = self.counts.get(key, 0)
        if count >= len(self.delays) or (key not in self.counts and len(self.counts) >= 128):
            return None

        self.counts[key] = count + 1
        return self.delays[count]


def get_browser_state(page) -> str:
    """Returns a compact hashable representation of the current page and active step."""
    from oracle_core.dom_interactor import get_question_identity, get_wizard_steps, is_step_current
    identity = get_question_identity(page)
    try:
        active = tuple(item.inner_text(timeout=1000) for _, item in get_wizard_steps(page) if is_step_current(item))
    except Exception:
        active = ()
    return repr((getattr(page, "url", ""), identity, active))
