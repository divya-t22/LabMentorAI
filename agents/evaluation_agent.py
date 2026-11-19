"""Evidence-only static evaluation. Student code is never executed here."""
from tools.code_analysis import analyze
from tools.assignment_relevance import assess_relevance


def evaluate(state, assignment_description='', test_cases=None):
    state.current_agent = 'Evaluation Agent'
    report = analyze(state.source_code)
    state.compilation_status = 'Valid Python syntax' if report['syntax_ok'] else 'Syntax error'
    relevance = assess_relevance(assignment_description, state.source_code)
    state.assignment_relevance = relevance
    # Quality observations from unrelated code are noise when assignment
    # relevance is already in doubt; defer those observations to faculty.
    if not relevance['requires_review']:
        state.quality_issues.extend(report['issues'])
    state.execution_status = 'NOT RUN — secure sandbox unavailable'
    state.test_results = []
    state.rubric_results = [
        {'criterion': name, 'maximum': points, 'awarded': None,
         'status': 'NOT EVALUATED', 'evidence_type': 'unavailable',
         'evidence': 'No secure execution or criterion-specific correctness evidence is available.'}
        for name, points in state.rubric.items()
    ]
    if not report['syntax_ok']:
        state.errors.append(report['syntax_error'])
    if relevance['requires_review']:
        state.errors.append('Assignment relevance is uncertain or mismatched; correctness scoring requires faculty review.')
    state.proposed_marks = 0.0
    state.status = 'Needs review' if relevance['requires_review'] or not report['syntax_ok'] else 'Evaluated'
    state.decisions.extend([
        {'agent':'Evaluation Agent','action':'static_python_ast_validation',
         'detail':f"Static AST syntax check: {state.compilation_status}. This verifies parseability only, not program correctness."},
        {'agent':'Evaluation Agent','action':'assignment_relevance_check',
         'detail':'; '.join(relevance['evidence']) if relevance['evidence'] else 'No high-confidence mismatch detected; relevance is not proof of correctness.'},
        {'agent':'Evaluation Agent','action':'execution_status',
         'detail':'NOT RUN: no secure sandbox is configured. Source was not executed; configured test cases were not run.'},
        {'agent':'Evaluation Agent','action':'rubric_scoring',
         'detail':'All rubric criteria are NOT EVALUATED because correctness evidence is unavailable. Proposed marks are 0 pending faculty review; no unsupported credit was awarded.'},
    ])
    if relevance['requires_review']:
        state.decisions.append({'agent':'Evaluation Agent','action':'quality_analysis_deferred',
            'detail':'Code-quality observations were suppressed because the source appears unrelated to the assignment; faculty review should establish relevance first.'})
    elif report['issues']:
        state.decisions.append({'agent':'Evaluation Agent','action':'static_quality_observations',
            'detail':'; '.join(report['issues'])})
    return report
