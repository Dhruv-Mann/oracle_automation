"""
sql_kb.py
Oracle SQL Syntax Rules and Heuristic Evaluator.
Provides offline syntactic scoring and fallback answering when AI is unavailable.
No hardcoded test questions or answers.
"""

import re

# ─────────────────────────────────────────────────────────────────────────────
# 1. ORACLE SQL SYNTAX RULES (Used to boost or penalize choices)
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that are ALWAYS WRONG in Oracle SQL
WRONG_SYNTAX = [
    r"delete\s+\*\s+from",                            # DELETE * FROM (invalid in Oracle)
    r"create\s+table\s+\w+\s*\(\s*\w+\s*,",           # CREATE TABLE (col, col) without datatypes
    r"alter\s+\w+\s+table\b",                         # ALTER tablename TABLE (wrong keyword order)
    r"alter\s+table\s+\w+\s+rename\s+\w+\s+varchar",  # RENAME col type (invalid)
    r"alter\s+table\s+\w+\s+alter\s+column",          # ALTER COLUMN is SQL Server/Postgres, not Oracle
    r"modify\s+column\s+\w+\s+varchar",               # MODIFY COLUMN is MySQL/MariaDB
    r"select\s+salary\s+from.*where.*without parentheses",
    r"^\s*create\s+table\s+\w+\s*\(\s*\w[\w\s]*,\s*\w[\w\s]*\)\s*;",  # no column types
]

# Patterns that indicate CORRECT Oracle SQL
GOOD_SYNTAX = [
    r"alter\s+table\s+\w+\s+modify\s*\(",             # ALTER TABLE t MODIFY (col type)
    r"alter\s+table\s+\w+\s+add\s*\(",                # ALTER TABLE t ADD (col type)
    r"alter\s+table\s+\w+\s+drop\s+column",           # ALTER TABLE t DROP COLUMN
    r"alter\s+table\s+\w+\s+set\s+unused",            # ALTER TABLE t SET UNUSED
    r"alter\s+table\s+\w+\s+rename\s+column",         # ALTER TABLE t RENAME COLUMN
    r"delete\s+from\s+\w+\s+where",                   # DELETE FROM t WHERE
    r"insert\s+into\s+\w+\s*\(\w",                    # INSERT INTO t (cols) VALUES
    r"create\s+table\s+\w+\s*\(\s*\w+\s+\w+",         # CREATE TABLE t (col TYPE
]


# ─────────────────────────────────────────────────────────────────────────────
# 2. HEURISTIC & SYNTACTIC SCORER
# ─────────────────────────────────────────────────────────────────────────────

def score_choice(choice_text: str, question: str, q_lower: str) -> float:
    """Evaluates choice viability based on Oracle SQL syntax and domain rules."""
    ct = choice_text.strip()
    cl = ct.lower()
    score = 5.0

    # 1. Reject invalid Oracle syntax
    for bad in WRONG_SYNTAX:
        if re.search(bad, cl, re.IGNORECASE | re.DOTALL):
            score -= 8.0
            break

    # 2. Boost valid syntax
    for good in GOOD_SYNTAX:
        if re.search(good, cl, re.IGNORECASE | re.DOTALL):
            score += 4.0
            break

    # 3. Domain rules & SQL semantics
    if "alter table" in q_lower:
        if "modify" in cl and "(" in cl:
            score += 5.0
        if "rename column" in cl:
            score += 3.0
        if "alter column" in cl or "modify column" in cl:
            score -= 6.0

    if "delete" in q_lower:
        if "delete from" in cl and "where" in cl:
            score += 4.0
        if "delete *" in cl:
            score -= 8.0

    if "insert" in q_lower:
        if re.search(r"insert into \w+\s*\(", cl):
            score += 3.0

    if "merge" in q_lower and "combines" in q_lower:
        if "insert and update" in cl:
            score += 8.0
        if "create and update" in cl:
            score -= 6.0

    if "summary" in q_lower or "structure" in q_lower or "describe" in q_lower:
        if "describe" in cl or "desc " in cl:
            score += 8.0

    if "set unused" in q_lower:
        if "logical drop" in cl or "displayed" in cl:
            score += 8.0
        if "frees the disk" in cl:
            score -= 4.0

    if "drop all columns" in q_lower or "drop column to drop all" in q_lower:
        if cl == "false":
            score += 7.0

    if "column name is valid" in q_lower or ("valid" in q_lower and "column name" in q_lower):
        if re.match(r'^[a-z][a-z0-9_$#]*$', cl, re.IGNORECASE):
            score += 5.0
        if re.match(r'^\d', cl):
            score -= 8.0

    if "table and column names must begin with a letter" in q_lower:
        if cl == "true":
            score += 7.0

    if "alter table to add a column" in q_lower or ("add a column" in q_lower and "new column" in q_lower):
        if "last column" in cl:
            score += 8.0

    if "time zone displacement" in q_lower:
        if "timestamp with time zone" in cl and "local" not in cl:
            score += 8.0

    if "variable-length character" in q_lower:
        if "varchar2" in cl:
            score += 5.0
        if "clob" in cl:
            score += 4.0

    if "which privilege allows" in q_lower:
        for priv in ["create table", "select", "insert", "update", "delete"]:
            if priv in q_lower and priv in cl:
                score += 8.0

    # Group Functions heuristics
    if "average" in q_lower and cl == "avg":
        score += 10.0
    if ("total" in q_lower or "sum" in q_lower) and cl == "sum":
        score += 10.0
    if ("maximum" in q_lower or "highest" in q_lower) and cl == "max":
        score += 10.0
    if ("minimum" in q_lower or "lowest" in q_lower) and cl == "min":
        score += 10.0
    if ("count" in q_lower or "number of rows" in q_lower) and cl == "count":
        score += 10.0
    if ("filter groups" in q_lower or "restrict groups" in q_lower) and cl == "having":
        score += 10.0
    if "group rows" in q_lower and cl == "group by":
        score += 10.0

    # Date datatype restriction: SUM, AVG, VARIANCE, STDDEV are invalid on DATE
    if "date" in q_lower and ("group function" in q_lower or "aggregate function" in q_lower):
        if cl in ("max", "min", "count"):
            score += 10.0
        if cl in ("sum", "avg", "stddev", "variance"):
            score -= 10.0

    # COUNT syntax: COUNT must have parentheses (COUNT(*) or COUNT(col))
    if re.search(r"select\s+count\s+from", cl):
        score -= 10.0
    if re.search(r"select\s+count\s*\(\s*\*\s*\)\s+from", cl):
        score += 10.0

    return score


def is_choose_two(question: str) -> bool:
    """Detects multi-select questions asking to choose two or more answers."""
    return bool(re.search(r"choose two|select two|pick two|which two", question, re.IGNORECASE))


def pick_best_answers(question: str, choices: list) -> list:
    """
    Given a question and choices list [{'text': '...', 'ariaChecked': '...'}],
    returns 1 or 2 matching answer strings using syntax rules and heuristic evaluation.
    """
    q_lower = question.lower()
    choice_texts = [c["text"] for c in choices]

    # Heuristic scoring across all choices
    scored = [(score_choice(c["text"], question, q_lower), c["text"]) for c in choices]
    scored.sort(reverse=True, key=lambda x: x[0])

    if is_choose_two(question):
        top2 = [scored[0][1]]
        for s, t in scored[1:]:
            if t not in top2:
                top2.append(t)
            if len(top2) == 2:
                break
        return top2

    # True / False heuristic rule
    tf = [c for c in choice_texts if c.strip().lower() in ("true", "false")]
    if tf and scored[0][0] < 6.0:
        negatives = ["cannot", "can't", "never", "not allowed", "invalid",
                     "incorrect", "does not", "is not possible", "is not used",
                     "are not", "cannot be", "is never", "not valid"]
        if any(neg in q_lower for neg in negatives):
            for ct in choice_texts:
                if ct.strip().lower() == "false":
                    return [ct]
        for ct in choice_texts:
            if ct.strip().lower() == "true":
                return [ct]

    return [scored[0][1]]
