"""
quiz_feedback.py
Parses assessment scores and retains feedback history across quiz retry attempts.
"""
import os
import json
import re
from oracle_core.dom_interactor import active_surfaces, extract_page_text

FEEDBACK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assessment_feedback.json"
)


def normalize_question_key(text: str) -> str:
    """Normalizes question text for consistent dictionary indexing."""
    cleaned = re.sub(r"^\s*Question\s+\d+\s+of\s+\d+\s*", "", text, flags=re.I)
    return " ".join(cleaned.casefold().split())


def get_question_feedback(question_text: str) -> list:
    """Retrieves previous incorrect answers and feedback recorded for this question."""
    if not os.path.exists(FEEDBACK_FILE):
        return []
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get(normalize_question_key(question_text), [])
    except Exception:
        return []


def save_assessment_results(page):
    """
    Scrapes score and question feedback from the results summary table.
    Returns the parsed score percentage (float) or None.
    """
    match = re.search(r"Percentage Scored\s*:\s*(\d+(?:\.\d+)?)\s*%", extract_page_text(page), re.I)
    score = float(match.group(1)) if match else None

    feedback_db = {}
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                feedback_db = json.load(f)
        except Exception:
            feedback_db = {}

    for frame in active_surfaces(page):
        try:
            rows = frame.locator('table.t-Report-report tbody tr').evaluate_all("""rows => rows.map(row =>
                Object.fromEntries([...row.querySelectorAll('td[headers]')].map(cell =>
                    [cell.getAttribute('headers'), cell.innerText.trim()])))""")
            for row in rows:
                if 'SCORE' in row and re.fullmatch(r'\d+(?:\.\d+)?%', row['SCORE']):
                    score = float(row['SCORE'].rstrip('%'))
                if row.get('QUESTION') and row.get('USER_CHOICE'):
                    key = normalize_question_key(row['QUESTION'])
                    entry = {'answer': row['USER_CHOICE'], 'feedback': row.get('INCORRECT_RESPONSE_FEEDBACK', '')}
                    if entry not in feedback_db.setdefault(key, []):
                        feedback_db[key].append(entry)
        except Exception:
            pass

    if feedback_db:
        try:
            with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
                json.dump(feedback_db, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return score
