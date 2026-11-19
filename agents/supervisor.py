"""Bounded conditional orchestrator for exactly three logical agents."""
from agents.evaluation_agent import evaluate
from agents.learning_agent import generate

MAX_DECISIONS = 5
ALLOWED_ACTIONS = {'evaluate', 'investigate_syntax', 'investigate_execution', 'investigate_performance', 'investigate_outputs', 'analyze_quality', 'personalize_feedback', 'request_review', 'finish'}

def run(state, student_name='Student', assignment_description='', test_cases=None):
    state.current_agent = 'Supervisor'
    try:
        evaluate(state, assignment_description, test_cases)
        state.decisions.append({'agent':'Supervisor', 'action':'delegate_evaluation', 'detail':'Evaluation Agent returned static syntax and relevance observations. Execution and test cases are NOT RUN because no secure sandbox is configured.'})
        if not state.source_code.strip(): action = 'finish'
        elif state.compilation_status != 'Valid Python syntax': action = 'investigate_syntax'
        elif state.assignment_relevance.get('requires_review'): action = 'investigate_outputs'
        elif state.test_results and any(not x['passed'] for x in state.test_results): action = 'investigate_outputs'
        else: action = 'request_review'
        state.next_action = action
        state.decision_count += 1
        if action not in ALLOWED_ACTIONS or state.decision_count > MAX_DECISIONS: raise ValueError('Supervisor action limit exceeded')
        reasons = {
            'finish': 'Submission is empty; skipped syntax, execution, and output investigation.',
            'investigate_syntax': f"Static parser found: {state.errors[0] if state.errors else 'invalid Python syntax'}; execution was skipped.",
            'investigate_performance': 'Static performance evidence requires review; source was not executed.',
            'investigate_execution': 'Execution evidence requires a secure runner; source was not executed.',
            'investigate_outputs': 'Assignment relevance is uncertain; correctness scoring stopped and faculty review was requested.',
            'analyze_quality': 'Selected static code-quality review; correctness remains unverified.',
            'request_review': 'No secure sandbox is configured, so tests and correctness are NOT EVALUATED; requested faculty review.',
        }
        state.decisions.append({'agent':'Supervisor', 'action':action, 'detail':reasons[action]})
        history = state.previous_history
        if history.get('mistakes') or history.get('records') or history.get('marks') or history.get('labs'):
            state.decisions.append({'agent':'Supervisor', 'action':'delegate_learning', 'detail':'A prior approved learning profile exists in the shared state; requested Learning Agent retrieval and comparison.'})
        else:
            state.decisions.append({'agent':'Supervisor', 'action':'delegate_learning', 'detail':'Requested Learning Agent retrieval; it will verify first-time status from SQLite without assuming prior performance.'})
        generate(state, student_name)
        state.decisions.append({'agent':'Supervisor', 'action':'validate_final_record', 'detail':'Validated evidence-backed feedback and proposed mark; faculty approval remains mandatory.'})
        return state
    except Exception as exc:
        state.status = 'Needs review'
        state.errors.append(f'Supervisor stopped safely: {exc}')
        return state
