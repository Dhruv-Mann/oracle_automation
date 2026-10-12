# Oracle Academy Autonomous Engine

An autonomous, AI-driven automation framework for Oracle Academy curriculum courses (Oracle APEX). Connects to a live Google Chrome instance via the **Chrome DevTools Protocol (CDP)** with zero hardcoded session tokens or credentials, and solves assessments using multi-account **Google Gemini & Gemma AI models**.

---

## Quick Start (Zero Friction — No Code Required)

For students and end users who want the bot to run immediately without installing Python, Git, or dependencies:

### 1. Download the Release ZIP
Go to the [Releases](https://github.com/Dhruv-Mann/oracle_automation/releases) section and download **`Oracle-Academy-Auto-v1.0.zip`**.

### 2. Extract
Right-click `Oracle-Academy-Auto-v1.0.zip` and select **Extract All**.

### 3. Add your Gemini API Key in `gemini_key.txt`

To enable the Gemini AI engine to solve all quizzes, midterms, and final exams with 100% accuracy:

1. **Get a Free API Key (30 seconds):**
   - Visit [Google AI Studio](https://aistudio.google.com/app/apikey).
   - Click **Create API key** (it is 100% free).

2. **Open `gemini_key.txt`:**
   - In the extracted folder, open **`gemini_key.txt`** in Notepad.
   - *(If `gemini_key.txt` is not there, create a new text file named `gemini_key.txt` next to `OracleAuto.exe`)*.

3. **Paste Your Key and Save:**
   - Paste your API key into the file:
     ```text
     AIzaSyExampleKeyFromGoogleStudio...
     ```
   - **Multi-Account Pooling (Optional):** You can paste multiple keys (one per line) from different Google accounts. The engine pools them, round-robins requests, and switches accounts instantly if one hits a rate limit.
   - Save the file (`Ctrl + S`) and close Notepad.

> [!NOTE]
> If `gemini_key.txt` is left empty, the engine automatically operates in **Offline Mode** using built-in Oracle SQL heuristic rules.

### 4. Run
Double-click **`OracleAuto.exe`** (or `RUN_ORACLE_AUTO.bat`):
1. Google Chrome automatically launches with remote debugging enabled.
2. Sign into your Oracle Academy student account in the Chrome window.
3. The engine automatically detects your login and executes the full multi-course loop:
   - Advances through all lesson slides (*"Save and Continue"*).
   - Automatically opens and solves Section Quizzes, Midterm Exams, and Final Exams.
   - Auto-retries any quiz scoring below 70%.
   - Sequentially completes all 3 courses until 100% finished.

---

## Key Features

- **One-Click Autonomous Multi-Course Loop:**
  Iterates through all 3 courses in sequence. For each section: plays all lesson slides, then launches the quiz/exam, solves all questions with AI, submits, verifies score, auto-retries if score < 70%, and loops until every section is 100% complete.

- **Auto-Retry on Low Scores:**
  If a quiz scores below 70%, the engine automatically re-takes it (up to 2 times) with adaptive answer selection.

- **Session Timeout & Multi-Tab Recovery:**
  Monitors and dismisses session timeout popups automatically. If full re-login is required, the engine scans across all browser tabs, brings the active student session to front, and resumes without losing progress.

- **Multi-Account AI Key Pooling & Auto-Failover:**
  Paste multiple Google Gemini API keys (from different accounts) into `gemini_key.txt`. The engine:
  - Round-robins requests across keys to multiply free-tier rate limits.
  - Instantly switches accounts if one hits a rate limit (`HTTP 429`).
  - Auto-ejects dead or invalid keys (`HTTP 401/403`) without interrupting the exam.

- **Dynamic Multi-Model Fallback Chain:**
  Sequentially cascades through modern Gemini and Gemma models:
  `gemini-flash-lite-latest` -> `gemini-3.5-flash-lite` -> `gemini-3.1-flash-lite` -> `gemma-4-26b` -> `gemini-3.6-flash` -> `gemini-3.5-flash` -> `gemma-4-31b` -> `gemini-3.8-flash`.

- **Offline Heuristic & Syntax Fallback:**
  If offline or no API keys are configured, an internal Oracle SQL syntax and heuristic engine evaluates choices based on SQL grammar, constraints, group functions, and null handling.

- **Zero Credential Storage & Safe CDP Connection:**
  No passwords or session cookies are ever stored. You log into Oracle Academy manually in Chrome; the engine attaches non-intrusively via CDP port 9222.

- **Human-Like Interaction:**
  Randomized pacing (2.0s–3.2s) and click limits (capped at 150 clicks/section) prevent runaway loops and reduce automation detection.

---

## Project Architecture

```
oracle_automation/
├── Oracle-Academy-Auto-v1.0.zip # Standalone zero-friction distribution
├── OracleAuto.exe            # Precompiled single-click executable
├── RUN_ORACLE_AUTO.bat       # One-click batch launcher (Chrome + Engine)
├── oracle_runner.py          # Master orchestrator & entrypoint
├── build_exe.py              # PyInstaller compilation script
├── requirements.txt          # Python dependencies
├── gemini_key.example.txt    # Template for API key configuration
└── oracle_core/
    ├── engine.py             # Course traversal, section progression & execution driver
    ├── dom_interactor.py     # Frame-aware APEX interaction, question & option locator
    ├── session_keeper.py     # Session timeout monitor & automatic extension
    ├── quiz_feedback.py     # Score recording & persistent feedback memory for retries
    ├── backoff_policy.py     # Adaptive retry budget & exponential backoff policy
    ├── logger.py             # Standardized logging, screenshot & DOM snapshot capture
    ├── ai_solver.py          # 8-model Gemini/Gemma chain with multi-key pooling
    └── sql_kb.py             # Offline Oracle SQL syntax rules & heuristic scorer
```

---

## For Nerds / Coders (CLI & Developer Guide)

For developers wanting to inspect, run, or modify the Python source code directly:

### 1. Clone the Repository

```bash
git clone https://github.com/Dhruv-Mann/oracle_automation.git
cd oracle_automation
```

### 2. Environment Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure API Keys

```bash
cp gemini_key.example.txt gemini_key.txt
```

Add your Gemini API key(s) into `gemini_key.txt` (one key per line for multi-account pooling).

```bash
python oracle_runner.py
```

This connects to your active Chrome browser (or launches Chrome with student profile), checks your `gemini_key.txt` pool, and executes the complete autonomous multi-course loop.

### Building the Standalone Executable

To compile `oracle_runner.py` and all modules into a self-contained Windows `.exe`:

```bash
python build_exe.py
```

The resulting executable is generated in `dist/OracleAuto.exe`.

---

## Security & Privacy

- **No credential handling:** This application never requests, handles, or saves Oracle passwords or login tokens.
- **Excluded credentials:** `gemini_key.txt`, `.env`, and `chrome_session/` are strictly ignored by `.gitignore`.
- **Isolated profile:** Automation executes within an isolated `chrome_session/` profile to keep your personal Chrome settings undisturbed.

---

## Disclaimer

This project is created for educational and personal workflow efficiency. Always comply with Oracle Academy terms of service and institutional academic policies.
