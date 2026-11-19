"""Deterministic learning feedback backed by approved SQLite history."""
from tools.learning_comparison import compare_history


def _unique(items):
    return list(dict.fromkeys(item for item in items if item))


def _weak_concepts(errors, quality):
    text = ' '.join(errors + quality).lower()
    concepts = []
    if 'syntax' in text or 'indent' in text:
        concepts.append('Python syntax and function structure')
    if 'runtime' in text or 'exception' in text:
        concepts.append('runtime errors and debugging')
    if 'timeout' in text or 'loop' in text or 'performance' in text:
        concepts.append('loop termination and performance')
    if 'output' in text or 'check' in text or 'edge' in text:
        concepts.append('test reasoning and edge cases')
    if quality:
        concepts.append('readability and code quality')
    return _unique(concepts)


def generate(state, student_name='Student'):
    """Consume evaluation evidence and real prior approvals; never infer absent history."""
    state.current_agent = 'Learning Agent'
    history = state.previous_history or {}
    records = history.get('records', [])
    # Existing versions stored actual approved marks in this array without lab labels.
    prior_marks = [mark for mark in history.get('marks', []) if isinstance(mark, (int, float))]
    if not prior_marks:
        prior_marks = [record.get('mark') for record in records if isinstance(record.get('mark'), (int, float))]
    is_returning = bool(records or history.get('labs', 0) or prior_marks)
    current_issues = _unique(state.errors + state.quality_issues)
    current_weak = _weak_concepts(state.errors, state.quality_issues)
    prior_record = records[-1] if records else None
    past_mistakes = prior_record.get('mistakes', []) if prior_record else []
    prior_weak = prior_record.get('weak_concepts', []) if prior_record else history.get('weak_concepts', [])
    prior_strengths = history.get('strengths', [])

    passed = sum(1 for result in state.test_results if result['passed'])
    total = len(state.test_results)
    high_performance = state.proposed_marks >= 8
    if state.assignment_relevance.get('requires_review'):
        current_strengths = []
    elif state.compilation_status == 'Valid Python syntax':
        current_strengths = ['Static syntax validation passed.']
    else:
        current_strengths = ['You submitted code that can be reviewed.'] if state.source_code.strip() else []
    if total and passed:
        current_strengths.append(f'{passed}/{total} configured demo checks were marked passed (simulated).')
    strengths = _unique(current_strengths + prior_strengths[:2])

    if high_performance:
        improvements = current_issues or ['No specific issue was found by the available static observations; correctness remains unverified.']
        practice = [
            'Compare two approaches and explain their time and space complexity.',
            'Extend the solution with larger inputs and property-based edge checks.',
        ]
        concepts = ['algorithmic complexity', 'data-structure trade-offs', 'property-based testing']
        level = 'Strong current result: extend your work through optimization and deeper algorithm analysis.'
    else:
        improvements = current_issues or ['Available evidence is limited; verify expected behavior with small examples.']
        practice = [
            'Write down the input, expected output, and one boundary case before coding.',
            'Trace the function one line at a time on a small example, then retest after each change.',
        ]
        concepts = current_weak or ['breaking a problem into steps', 'testing normal and boundary inputs']
        level = 'Start with the specific evidence below, then rebuild the solution in small testable steps.'

    comparison=compare_history(prior_record,state.evidence_topics,prior_marks,state.proposed_marks,current_issues,state.compilation_status)
    previous_mark=comparison['previous_approved_mark']
    improved=comparison['improved_concepts']
    recurring=_unique([item['mistake'] for item in comparison['current_mistake_comparison'] if item['status']=='repeated'])
    if previous_mark is None:
        trend='No prior approved mark is stored.'
    else:
        trend=f"Previous approved mark: {previous_mark:g}/10; current proposed mark: {state.proposed_marks:g}/10. Difference: {comparison['mark_difference']:+g}; this numeric difference alone is not evidence of learning. Current correctness remains NOT EVALUATED and awaits faculty review."

    feedback = {
        'student': student_name,
        'summary': 'DETERMINISTIC FEEDBACK MODE: recommendations use static observations and approved SQLite history. No LLM was called; execution and correctness were not evaluated.',
        'learner_status': 'Returning student' if is_returning else 'First-time student (no prior approved labs found)',
        'historical_labs': max(len(records), int(history.get('labs', 0) or 0), len(prior_marks)),
        'historical_marks': prior_marks,
        'previous_mark': previous_mark,
        'trend': trend,
        **comparison,
        'previous_observed_mistakes':prior_record.get('mistakes',[]) if prior_record else [],
        'current_observed_mistakes':current_issues,
        'previous_evaluation_topics':comparison['previous_topics'],
        'strengths': strengths,
        'previous_strengths': prior_strengths,
        'improvements': improvements,
        'hints': practice[:2],
        'concepts': _unique(concepts + prior_weak[:2]),
        'current_weak_concepts': current_weak,
        'previous_weak_concepts': prior_weak,
        'recurring': recurring,
        'improved_concepts': improved,
        'practice': practice,
        'personalization': level,
        'status': 'Proposed marks require faculty approval.',
    }
    state.recurring_mistakes = recurring
    state.feedback = feedback

    if is_returning:
        record_ids=', '.join(str(record['submission_id']) for record in records) or 'legacy approved mark record(s)'
        history_evidence = f"Queried approved evaluations in SQLite for student_id={state.student_id}; retrieved {len(records) if records else len(prior_marks)} approved record(s) (submission IDs: {record_ids}); marks: {', '.join(f'{m:g}' for m in prior_marks) or 'not available'}; prior weaknesses: {', '.join(prior_weak) or 'not recorded'}; prior strengths: {', '.join(prior_strengths) or 'not recorded'}. Unapproved rows and profile-only values are excluded."
    else:
        history_evidence = f'Queried approved evaluations in SQLite for student_id={state.student_id}; no earlier faculty-approved evaluation exists. Unapproved rows and profile-only values are excluded; no history was inferred.'
    state.decisions.append({'agent': 'Learning Agent', 'action': 'retrieve_learning_history', 'detail': history_evidence})
    compare_evidence = f"Comparison status: {comparison['topic_comparability']}; previous approved mark={previous_mark if previous_mark is not None else 'none'}, current proposed mark={state.proposed_marks:g}/10; previous findings: {', '.join(item['status']+': '+item['mistake'] for item in comparison['previous_mistake_comparison']) or 'none'}; current findings: {', '.join(item['status']+': '+item['mistake'] for item in comparison['current_mistake_comparison']) or 'none'}; improvement status={comparison['improvement_status']}. A mark increase alone was not treated as improvement."
    state.decisions.append({'agent': 'Learning Agent', 'action': 'compare_performance', 'detail': compare_evidence})
    state.decisions.append({'agent': 'Learning Agent', 'action': 'personalize_recommendations', 'detail': f"{level} Recommendations: {'; '.join(practice)}"})
    return feedback
