"""
ai_solver.py
AI-Powered Question Solver using Google Gemini API.
Features:
- Multi-Account API Key Pooling & Automatic Rotation (rotates instantly on 429 / quota limit)
- Dynamic fallback chain across fast modern Gemini models:
  1. gemini-3.6-flash
  2. gemini-3.5-flash
  3. gemini-3.5-flash-lite
  4. gemini-3.8-flash
- Strict JSON reasoning for Oracle Database 19c/21c curriculum questions.
"""

import os
import sys
import json
import re
import time
import warnings
warnings.filterwarnings("ignore")
import requests

MODELS_CHAIN = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemma-4-26b-a4b-it",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemma-4-31b-it",
    "gemini-3.8-flash"
]
DEFAULT_MODEL = MODELS_CHAIN[0]

_last_request_timestamp = 0.0


def get_api_keys() -> list[str]:
    """
    Retrieves all configured Gemini API keys from:
    1. Environment variables (GEMINI_API_KEY, GEMINI_API_KEYS)
    2. .env file
    3. gemini_key.txt in project root (supports multiple keys, one per line)
    """
    keys = []

    # 1. Environment variables
    for var in ("GEMINI_API_KEY", "GEMINI_API_KEYS"):
        val = os.environ.get(var, "").strip()
        if val:
            for k in val.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)

    # Candidate directories to search for key files
    candidate_dirs = [
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(),
        os.getcwd()
    ]
    if hasattr(sys, "_MEIPASS"):
        candidate_dirs.append(sys._MEIPASS)

    # 2. .env files
    for d in candidate_dirs:
        env_path = os.path.join(d, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GEMINI_API_KEY=") or line.startswith("GEMINI_API_KEYS="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            for k in val.split(","):
                                k = k.strip()
                                if k and k not in keys:
                                    keys.append(k)
            except Exception:
                pass

    # 3. gemini_key.txt in project directories (one key per line)
    for d in candidate_dirs:
        key_file = os.path.join(d, "gemini_key.txt")
        if os.path.exists(key_file):
            try:
                with open(key_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            for k in line.split(","):
                                k = k.strip()
                                if k and k not in keys:
                                    keys.append(k)
            except Exception:
                pass

    return keys


def get_api_key() -> str:
    """Returns the primary API key (for backward compatibility)."""
    keys = get_api_keys()
    return keys[0] if keys else ""


class KeyPool:
    """
    Manages a pool of API keys across multiple Google accounts.
    Supports round-robin load distribution and instant failover on quota exhaustion.
    """
    def __init__(self):
        self._keys = []
        self._current_idx = 0
        self._exhausted_keys = set()
        self._dead_keys = set()
        self._reload_keys()

    def _reload_keys(self):
        loaded = get_api_keys()
        self._keys = loaded

    def get_next_key(self) -> str:
        self._reload_keys()
        if not self._keys:
            return ""

        usable_keys = [k for k in self._keys if k not in self._dead_keys]
        if not usable_keys:
            print("  [KEY POOL] All keys in pool are invalid or unauthorized (HTTP 401).")
            return ""

        active_keys = [k for k in usable_keys if k not in self._exhausted_keys]
        if not active_keys:
            # All usable keys temporarily exhausted; reset pool so retries can proceed
            print("  [KEY POOL] All valid keys are temporarily rate-limited. Resetting rate-limit markers...")
            self._exhausted_keys.clear()
            active_keys = usable_keys

        self._current_idx = self._current_idx % len(active_keys)
        selected = active_keys[self._current_idx]
        self._current_idx = (self._current_idx + 1) % len(active_keys)
        return selected

    def mark_exhausted(self, key: str, reason: str = ""):
        mask = (key[:6] + "..." + key[-4:]) if len(key) > 10 else "key"
        self._exhausted_keys.add(key)
        remaining = self.active_count()
        print(f"  [KEY ROTATION] Key [{mask}] hit limit ({reason}). {remaining} active account key(s) remaining.")

    def mark_dead(self, key: str, reason: str = ""):
        mask = (key[:6] + "..." + key[-4:]) if len(key) > 10 else "key"
        self._dead_keys.add(key)
        remaining = self.active_count()
        print(f"  [KEY EJECTED] Key [{mask}] is invalid ({reason}). Ejected from pool! {remaining} valid key(s) remaining.")

    def active_count(self) -> int:
        self._reload_keys()
        return len([k for k in self._keys if k not in self._exhausted_keys and k not in self._dead_keys])

    def total_count(self) -> int:
        self._reload_keys()
        return len([k for k in self._keys if k not in self._dead_keys])


# Singleton key pool instance
_key_pool = KeyPool()


def _enforce_rate_limit(pool_size: int = 1):
    """
    Enforces request spacing based on pool size.
    With 1 key: 4.5s (safe for 15 RPM).
    With 2+ keys: scales down safely since requests are routed across separate accounts.
    """
    global _last_request_timestamp
    if pool_size >= 3:
        interval = 1.5
    elif pool_size == 2:
        interval = 2.5
    else:
        interval = 4.5

    now = time.time()
    elapsed = now - _last_request_timestamp
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_request_timestamp = time.time()


def solve_with_gemini(question: str, choices: list[dict], model_idx: int = 0, max_retries: int = 4, image_b64: str = None, multiple: bool = False) -> list[str]:
    """
    Calls Google Gemini API with multi-account key rotation, model fallbacks,
    multimodal vision support, and structured JSON response matching.
    """
    if _key_pool.total_count() == 0:
        raise ValueError(
            "No Gemini API keys found.\n"
            "Please paste your key(s) into gemini_key.txt (in the application folder),\n"
            "or set the GEMINI_API_KEY environment variable."
        )

    model_name = MODELS_CHAIN[model_idx % len(MODELS_CHAIN)]
    choice_texts = [c["text"].strip() for c in choices]
    formatted_choices = "\n".join([f"- {ct}" for ct in choice_texts])

    prompt = f"""You are an elite Oracle Database 19c/21c SQL expert and instructor for the Oracle Academy curriculum.
Answer this multiple-choice exam question with 100% accuracy according to Oracle SQL specifications and Oracle APEX rules.

Question:
{question}

Available Choices:
{formatted_choices}

Instructions:
1. Examine each choice against Oracle SQL syntax, data dictionary behavior, constraints, group functions, and null handling.
2. If the question specifies 'Choose Two', 'Select Two', or multi-select, include all correct choices in the array.
3. If it is a single-choice question, include only the single correct choice in the array.
4. Each answer string in the "answers" array MUST match the exact text of one of the available choices listed above.

Return ONLY a JSON object formatted exactly as:
{{
  "answers": ["exact choice text"],
  "reasoning": "one sentence explanation"
}}
"""

    gen_config = {"temperature": 0.1}
    if "gemma" not in model_name.lower():
        gen_config["responseMimeType"] = "application/json"

    parts = [{"text": prompt}]
    if image_b64 and "gemma" not in model_name.lower():
        parts.append({
            "inlineData": {
                "mimeType": "image/png",
                "data": image_b64
            }
        })

    payload = {
        "contents": [
            {
                "parts": parts
            }
        ],
        "generationConfig": gen_config
    }

    for attempt in range(1, max_retries + 1):
        api_key = _key_pool.get_next_key()
        active_keys = _key_pool.active_count()
        _enforce_rate_limit(pool_size=active_keys)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

        try:
            resp = requests.post(url, json=payload, timeout=20)

            # Handle 401 / 403 (Invalid, Revoked or Unauthorized API Key)
            if resp.status_code in (401, 403):
                _key_pool.mark_dead(api_key, f"HTTP {resp.status_code} Unauthorized/Invalid Key")
                if _key_pool.active_count() > 0:
                    print("  [KEY ROTATION] Immediately retrying with next valid key in pool...")
                    continue
                else:
                    raise RuntimeError(f"All configured API keys returned HTTP {resp.status_code} Unauthorized. Please check gemini_key.txt.")

            # Handle 429 (Rate Limit / Quota Exceeded)
            if resp.status_code == 429:
                # If we have multiple accounts in the pool, switch immediately!
                if _key_pool.active_count() > 1:
                    _key_pool.mark_exhausted(api_key, "HTTP 429 Rate/Daily Limit")
                    print("  [KEY ROTATION] Immediately trying next account key...")
                    continue

                # If only 1 key or all exhausted, try next model or pause
                if model_idx + 1 < len(MODELS_CHAIN):
                    print(f"  [!] {model_name} returned 429, switching to {MODELS_CHAIN[model_idx + 1]}...")
                    return solve_with_gemini(question, choices, model_idx=model_idx + 1)

                wait_sec = 8 + (attempt * 4)
                print(f"  [AI RATE LIMIT] Status 429. Pausing {wait_sec}s before retry ({attempt}/{max_retries})...")
                time.sleep(wait_sec)
                continue

            # Handle 503 (High demand) or 404 (Retired model)
            if resp.status_code in (503, 404):
                if model_idx + 1 < len(MODELS_CHAIN):
                    next_model = MODELS_CHAIN[model_idx + 1]
                    print(f"  [!] {model_name} returned {resp.status_code}, switching to {next_model}...")
                    return solve_with_gemini(question, choices, model_idx=model_idx + 1)
                wait_sec = 5 + (attempt * 3)
                print(f"  [!] Server busy ({resp.status_code}). Pausing {wait_sec}s...")
                time.sleep(wait_sec)
                continue

            if resp.status_code != 200:
                if model_idx + 1 < len(MODELS_CHAIN):
                    print(f"  [!] {model_name} returned HTTP {resp.status_code}, trying fallback model...")
                    return solve_with_gemini(question, choices, model_idx=model_idx + 1)
                raise RuntimeError(f"Gemini API error HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Handle markdown code blocks from Gemma models
            if "```" in raw_text:
                m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
                if m:
                    raw_text = m.group(1).strip()
            try:
                result = json.loads(raw_text)
            except Exception:
                m = re.search(r"\{[\s\S]*\}", raw_text)
                if m:
                    result = json.loads(m.group(0))
                else:
                    result = {"answers": [raw_text]}

            selected_raw = result.get("answers", [])
            reasoning = result.get("reasoning", "")
            if reasoning:
                print(f"  [AI REASONING] {reasoning}")

            # Match selected answers against available choices for exact character alignment
            matched_choices = []
            for ans in selected_raw:
                clean_ans = ans.strip().lower()
                # 1. Exact match
                matched = next((ct for ct in choice_texts if ct.lower() == clean_ans), None)
                # 2. Substring match
                if not matched:
                    matched = next((ct for ct in choice_texts if clean_ans in ct.lower() or ct.lower() in clean_ans), None)
                if matched and matched not in matched_choices:
                    matched_choices.append(matched)

            if matched_choices:
                return matched_choices

            return selected_raw if selected_raw else [choice_texts[0]]

        except requests.exceptions.RequestException as re_err:
            if attempt < max_retries:
                print(f"  [!] Network issue ({re_err}). Retrying in 3s ({attempt}/{max_retries})...")
                time.sleep(3)
            else:
                if model_idx + 1 < len(MODELS_CHAIN):
                    return solve_with_gemini(question, choices, model_idx=model_idx + 1)
                raise re_err


def validate_api():
    """Validates and announces API key pool status."""
    keys = get_api_keys()
    if keys:
        print(f"  [AI ENGINE] Google Gemini key pool ready: {len(keys)} key(s) pooled.")
        print(f"  [AI ENGINE] Model fallback chain: {len(MODELS_CHAIN)} models configured.")
    else:
        print("  [AI ENGINE] Notice: No Gemini API keys found. Operating in Offline Mode with built-in SQL heuristics.")


def solve_with_session(page, question_text: str, options: list[str], image_b64=None, multiple: bool = False) -> list[int]:
    """
    Main solver entry point.
    Takes question text, options (list of strings), optional image, and multiple flag.
    Returns list of 0-based integer option indices (e.g. [0] or [0, 2]).
    """
    from oracle_core.session_keeper import keep_session_alive
    from oracle_core.quiz_feedback import get_question_feedback
    from oracle_core.sql_kb import pick_best_answers
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    fb_entries = get_question_feedback(question_text)
    bad_answers = [e["answer"] for e in fb_entries if isinstance(e, dict) and "answer" in e]

    choices = [{"text": opt} for opt in options]

    def _worker():
        if _key_pool.total_count() > 0:
            prompt_q = question_text
            if bad_answers:
                prompt_q += f"\n[NOTICE: In a previous attempt, these answer(s) were marked INCORRECT: {bad_answers}. Do NOT select these!]"
            if multiple:
                prompt_q += "\n[Note: This is a MULTI-SELECT question. Choose all correct options.]"
            else:
                prompt_q += "\n[Note: This is a SINGLE-CHOICE question. Choose only the single best option.]"

            try:
                chosen_texts = solve_with_gemini(prompt_q, choices, image_b64=image_b64, multiple=multiple)
                matched_indices = []
                for ans in chosen_texts:
                    clean_ans = ans.strip().lower()
                    for idx, opt in enumerate(options):
                        clean_opt = opt.strip().lower()
                        if clean_opt == clean_ans or clean_opt.startswith(clean_ans) or clean_ans in clean_opt:
                            if idx not in matched_indices:
                                matched_indices.append(idx)
                                break
                if matched_indices:
                    if not multiple and len(matched_indices) > 1:
                        matched_indices = matched_indices[:1]
                    return matched_indices
            except Exception as e:
                print(f"  [AI ENGINE] Gemini attempt error: {e}. Falling back to SQL heuristics...")

        # Fallback to offline SQL Knowledge Base
        chosen_texts = pick_best_answers(question_text, choices)
        matched_indices = []
        for ans in chosen_texts:
            clean_ans = ans.strip().lower()
            for idx, opt in enumerate(options):
                clean_opt = opt.strip().lower()
                if clean_opt == clean_ans or clean_ans in clean_opt:
                    if idx not in matched_indices:
                        matched_indices.append(idx)
        if not matched_indices:
            matched_indices = [0]
        if not multiple and len(matched_indices) > 1:
            matched_indices = matched_indices[:1]
        return matched_indices

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_worker)
        while True:
            keep_session_alive(page)
            if future.done():
                return future.result()
            try:
                return future.result(timeout=0.25)
            except FutureTimeout:
                if future.done():
                    return future.result()
                page.wait_for_timeout(250)

