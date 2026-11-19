# LabMentor AI

Local Flask + SQLite prototype for faculty review and student learning feedback. Evaluation uses static Python AST analysis and conservative assignment relevance checks. Student submissions are never executed by the Flask process. Since no secure runner is configured, execution and assignment test cases are reported as **NOT RUN**, rubric criteria as **NOT EVALUATED**, and unsupported proposed credit is not awarded. Feedback is deterministic and labelled; no LLM is called.

## Start on Windows

From this project folder in PowerShell:

```powershell
python -m pip install -r requirements.txt
python -m flask --app app init-db
python -m flask --app app run --debug
```

Open <http://127.0.0.1:5000>. Stop with Ctrl+C. `init-db` preserves existing records and applies additive schema migrations. Use **Initialize demo workspace** for sample records. Faculty can create assignments/students, submit Python source, review evidence, approve or reject, and export CSV. Student reports are at `/student/<student-code>` after approval.

The landing page is `/`; its Faculty Portal opens the existing dashboard at `/faculty`. The Student Portal asks for a student code and redirects to the existing `/student/<student-code>` report. This code selector is demo navigation only, not authentication.

## Implemented

- A LangGraph `StateGraph` coordinates the Supervisor, Evaluation, and Learning Agents through typed shared state. The Supervisor routes mismatch, missing evidence, syntax failure, verified test failure, and workflow errors explicitly.
- Graph execution happens outside SQLite transactions. Flask persists reports insert-only so workflow retries do not overwrite faculty decisions or duplicate evaluation event records.
- Static syntax and basic AST observations; an explicit mismatch detector for web applications submitted to the sample even-list-sum assignment.
- Proposed marks are separate from approved marks. Individual rubric rows show NOT EVALUATED without verified evidence; execution and test cases show NOT RUN.
- SQLite submissions, evaluation reports, trace events, similarity signals, approved-only learning history, faculty feedback, approval, rejection, student report, and CSV export.
- Safe additive migration for `evaluations.faculty_feedback`.
- One-time quarantine for pre-upgrade fixture evaluations: old displayed marks/test counts are preserved in a `legacy_quarantined` report snapshot, but the row is moved to **Needs review**, its proposed/final marks and counts are cleared, and old derived learning cache entries are cleared. Faculty can review and record a fresh approval.

## Limitations and security

- No sandbox is configured. Docker is unavailable, and arbitrary student code must not be run in the Flask process. Actual public/hidden test evaluation, correctness scoring, and criterion-level marks remain incomplete.
- Relevance checks are conservative and do not prove that code solves an assignment. Similarity is a token-level review signal, not a misconduct finding.
- There is no login, role-based authorization, or CSRF protection. Student report URLs use student codes. Keep the development server on localhost; do not expose this prototype to a network.
- There is no LLM integration. Learning Agent feedback is deterministic.
- Test execution remains NOT RUN until an isolated sandbox is implemented. The graph's verified-test routing branch is ready for sandbox-proven evidence, but this application does not generate that evidence.
- Git metadata was absent in the supplied workspace. A pre-edit source backup is stored in `backups/pre-upgrade-20261119`.

## Verify

```powershell
python -m unittest discover -s tests -v
```

Tests use a temporary SQLite database. They cover graph construction and node routing, bounded recoverable-error retries, safe non-execution, mismatch handling, NOT EVALUATED rubric rows, pending faculty decisions, idempotent persistence, approval/rejection, learning memory, and CSV output.
