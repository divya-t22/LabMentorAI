# LabMentor AI audit (before consolidated upgrade)

## Existing features found
- Flask faculty dashboard, assignment/student/submission forms, report detail route, student report route, CSV export, and SQLite persistence.
- Three logical agents share an `EvaluationState`; the Supervisor selects a bounded next action and invokes deterministic Learning Agent feedback.
- Static AST checks, a narrow Flask-vs-even-sum relevance detector, token similarity flags, and approved-only historical comparisons.
- Demo initialization creates fixtures and submissions; all evaluation is labelled simulated and does not execute source code.

## Main risks and inconsistencies
- Evaluation Agent assigns three simulated outcomes to almost every syntactically valid submission without using assignment test cases. Its scoring multiplies total rubric points by that fabricated pass ratio, so unrelated Flask code can receive 10/10.
- `passed`/`total` are shown as test results despite being fixture outcomes, not student execution. Rubric criteria are not individually evidenced or scored.
- Relevance check is narrowly hard-coded to an even-list-sum prompt and only used at report rendering, not in scoring or supervisor routing.
- Evaluation reports persist JSON plus event rows; approval is persisted separately, but there is no rejection workflow or feedback/mark audit trail. Approval hardcodes faculty user id 1.
- Routes accept writes without authentication/authorization or CSRF protection; the default secret key is static. This is a local demo, not production-safe.
- Student route exposes reports by student code, with no identity authentication. CSV export includes proposed as well as approved outcomes.
- Assignment creation has weak validation; upload decodes invalid UTF-8 with replacement and does not reject empty source. Similarity flags can duplicate across re-evaluations.
- Agent implementation is deterministic rule-based Python, not LangGraph or an LLM. The UI correctly labels mock feedback but some areas describe simulated fixtures as checks.
- Tests currently encode the incorrect behavior (including 10/10 for a Flask mismatch and generic valid submissions); expectations need to change alongside evaluation behavior.

## Constraints and baseline
- No `.git` directory is present; Git status/checkpoint is unavailable. A recoverable source backup was created under `backups/pre-upgrade-20261119` before edits. The existing SQLite DB was left in place.
- `requirements.txt` only includes Flask; Docker/LangGraph/LLM execution is not configured.
