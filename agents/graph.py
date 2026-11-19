"""LangGraph orchestration adapters for LabMentor AI's existing agents.

The graph is intentionally in-memory: this phase adds no checkpointer, pause,
LLM, or automatic approval. SQLite reads and writes remain in the Flask layer.
"""
from typing import Any

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agents import evaluation_agent, learning_agent
from agents.graph_state import EvaluationGraphState
from agents.state import EvaluationState


class RecoverableInfrastructureError(Exception):
    """Marker for transient evaluator infrastructure failures only."""


MAX_INFRASTRUCTURE_ATTEMPTS = 2


def supervisor_dispatch_node(state: EvaluationGraphState) -> dict[str, Any]:
    return {
        'decisions': state.get('decisions', []) + [{
            'agent': 'Supervisor',
            'action': 'dispatch_evaluation',
            'detail': 'Assigned static syntax and relevance analysis to the Evaluation Agent; correctness evidence is not assumed.',
        }]
    }


def evaluation_node(state: EvaluationGraphState, evaluator=None) -> dict[str, Any]:
    """Adapt the existing Evaluation Agent to a typed graph-state update."""
    agent_state = EvaluationState(
        student_id=state['student_id'],
        assignment_id=state['assignment_id'],
        submission_id=state['submission_id'],
        source_code=state['source_code'],
        rubric=state['assignment_rubric'],
        previous_history=state.get('approved_learning_history', {}),
    )
    agent_state.evidence_topics = state.get('evidence_topics', [])
    agent_state.decisions = list(state.get('decisions', []))
    evaluator = evaluator or evaluation_agent.evaluate
    evaluator(agent_state, state['assignment_description'], test_cases=None)

    verified = [
        test for test in agent_state.test_results
        if test.get('verified') is True and test.get('source') == 'sandbox'
    ]
    evidence = {
        'verified': [
            f"Static AST syntax check: {agent_state.compilation_status}. This verifies parseability only, not behavior."
        ],
        'static_observations': agent_state.assignment_relevance.get('evidence', []) + agent_state.quality_issues,
        'not_evaluated': [
            'Program execution', 'Configured test cases', 'Program logic and correctness',
            *[item['criterion'] for item in agent_state.rubric_results],
        ],
    }
    return {
        'compilation_status': agent_state.compilation_status,
        'execution_status': agent_state.execution_status,
        # The current evaluator never runs cases. Do not copy test inputs or
        # historical fixtures into the graph even if an older caller supplied them.
        'test_results': verified,
        'assignment_relevance': agent_state.assignment_relevance,
        'quality_observations': agent_state.quality_issues,
        'evaluation_errors': agent_state.errors,
        'evidence': evidence,
        'rubric_breakdown': agent_state.rubric_results,
        'proposed_marks': 0.0,
        'evaluation_status': agent_state.status,
        'decisions': agent_state.decisions,
    }


def select_route(state: EvaluationGraphState) -> tuple[str, str]:
    """Choose the next edge using provenance-checked current evidence."""
    if state.get('error_info'):
        return 'workflow_error', 'An agent node failed; stopped automatic evaluation and requested faculty review.'
    if state.get('assignment_relevance', {}).get('requires_review'):
        return 'assignment_mismatch', 'Assignment relevance is uncertain; correctness scoring stops pending faculty review.'
    if state.get('compilation_status') != 'Valid Python syntax':
        return 'syntax_failure', 'Static syntax validation failed; routed to Learning Agent for syntax guidance.'

    results = state.get('test_results', [])
    execution_verified = state.get('execution_status', '').startswith('COMPLETED')
    verified_results = [
        test for test in results
        if test.get('verified') is True and test.get('source') == 'sandbox'
    ]
    if execution_verified and verified_results and any(not test.get('passed', False) for test in verified_results):
        return 'test_failure', 'Verified sandbox evidence contains a failed test; routed to Learning Agent for targeted guidance.'
    if execution_verified and results and len(verified_results) == len(results):
        return 'evidence_supported', 'Current isolated-execution evidence is available; routed to learning feedback and report consolidation.'
    return 'missing_evidence', 'Execution or verified test evidence is unavailable; correctness remains NOT EVALUATED and faculty review is required.'


def supervisor_route_node(state: EvaluationGraphState) -> dict[str, Any]:
    route, reason = select_route(state)
    return {
        'route_decision': route,
        'route_reason': reason,
        'decisions': state.get('decisions', []) + [{
            'agent': 'Supervisor', 'action': f'route_{route}', 'detail': reason,
        }],
    }


def route_after_evaluation(state: EvaluationGraphState) -> str:
    return state.get('route_decision', 'workflow_error')


def faculty_review_node(state: EvaluationGraphState) -> dict[str, Any]:
    reason = state.get('route_reason') or 'Faculty review is required before finalizing this evaluation.'
    return {
        'faculty_review_required': True,
        'faculty_review_status': 'Pending',
        'evaluation_status': 'Needs review',
        'decisions': state.get('decisions', []) + [{
            'agent': 'Supervisor', 'action': 'request_faculty_review', 'detail': reason,
        }],
    }


def route_after_faculty_review(state: EvaluationGraphState) -> str:
    # Keep relevance warnings visible to faculty before any learning feedback.
    # Learning feedback is still deterministic and does not award marks.
    return 'learning_agent'


def learning_node(state: EvaluationGraphState, learner=None) -> dict[str, Any]:
    """Adapt the existing deterministic Learning Agent without scoring work."""
    agent_state = EvaluationState(
        student_id=state['student_id'],
        assignment_id=state['assignment_id'],
        submission_id=state['submission_id'],
        source_code=state['source_code'],
        rubric=state['assignment_rubric'],
        previous_history=state.get('approved_learning_history', {}),
    )
    agent_state.test_results = list(state.get('test_results', []))
    agent_state.compilation_status = state.get('compilation_status', 'Not checked')
    agent_state.execution_status = state.get('execution_status', 'NOT RUN — secure sandbox unavailable')
    agent_state.errors = list(state.get('evaluation_errors', []))
    agent_state.quality_issues = list(state.get('quality_observations', []))
    agent_state.assignment_relevance = state.get('assignment_relevance', {'requires_review': False, 'evidence': []})
    agent_state.proposed_marks = state.get('proposed_marks', 0.0)
    agent_state.evidence_topics = state.get('evidence_topics', [])
    agent_state.decisions = list(state.get('decisions', []))
    learner = learner or learning_agent.generate
    feedback = learner(agent_state, state.get('student_name', 'Student'))
    return {'learning_feedback': feedback, 'decisions': agent_state.decisions}


def safe_node_error_handler(state: EvaluationGraphState, error: NodeError) -> dict[str, Any]:
    """Convert non-retryable/exhausted node errors into a pending review record."""
    node = str(error.node)
    cause = error.error
    error_info = {'node': node, 'type': type(cause).__name__, 'message': str(cause)[:500]}
    return {
        'error_info': error_info,
        'route_decision': 'workflow_error',
        'route_reason': f'{node} failed safely; no correctness credit was awarded and faculty review is pending.',
        'evaluation_errors': state.get('evaluation_errors', []) + [f'{node} failed: {type(cause).__name__}'],
        'proposed_marks': 0.0,
        'faculty_review_required': True,
        'faculty_review_status': 'Pending',
        'evaluation_status': 'Needs review',
        'decisions': state.get('decisions', []) + [{
            'agent': 'Supervisor', 'action': 'safe_error_review',
            'detail': f'{node} failed after applicable retries; evaluation stopped for faculty review.',
        }],
    }


def safe_review_node(state: EvaluationGraphState) -> dict[str, Any]:
    return {
        'faculty_review_required': True,
        'faculty_review_status': 'Pending',
        'evaluation_status': 'Needs review',
        'proposed_marks': 0.0,
    }


def supervisor_consolidation_node(state: EvaluationGraphState) -> dict[str, Any]:
    failed = bool(state.get('error_info'))
    review_required = state.get('faculty_review_required', True) or failed
    if review_required:
        status = 'Needs review'
    else:
        status = 'Proposed'
    # This is the proposed evaluation report only. The existing faculty routes
    # remain the sole way to record Approved or Rejected.
    report = {
        'submission_id': state['submission_id'],
        'assignment_id': state['assignment_id'],
        'evaluation_status': status,
        'route_decision': state.get('route_decision'),
        'route_reason': state.get('route_reason'),
        'execution_status': state.get('execution_status', 'NOT RUN — secure sandbox unavailable'),
        'test_results': list(state.get('test_results', [])),
        'assignment_relevance': state.get('assignment_relevance', {}),
        'evidence': state.get('evidence', {}),
        'rubric_breakdown': list(state.get('rubric_breakdown', [])),
        'proposed_marks': 0.0,
        'faculty_review_status': 'Pending',
        'error_info': state.get('error_info'),
        'learning_feedback': state.get('learning_feedback', {}),
    }
    decisions = state.get('decisions', []) + [{
        'agent': 'Supervisor', 'action': 'consolidate_pending_report',
        'detail': 'Consolidated current evidence and limitations. Faculty approval or rejection is still required; no final mark was recorded.',
    }]
    return {
        'evaluation_status': status,
        'faculty_review_required': review_required,
        'faculty_review_status': 'Pending',
        'proposed_marks': 0.0,
        'final_report': report,
        'decisions': decisions,
    }


def build_evaluation_graph(*, evaluator=None, learner=None):
    """Compile the workflow; injections support isolated node and retry tests."""
    def run_evaluation(state: EvaluationGraphState):
        return evaluation_node(state, evaluator=evaluator)

    def run_learning(state: EvaluationGraphState):
        return learning_node(state, learner=learner)

    builder = StateGraph(EvaluationGraphState)
    builder.add_node('supervisor_dispatch', supervisor_dispatch_node, error_handler=safe_node_error_handler)
    builder.add_node(
        'evaluation_agent', run_evaluation,
        retry_policy=RetryPolicy(
            max_attempts=MAX_INFRASTRUCTURE_ATTEMPTS,
            initial_interval=0.01,
            max_interval=0.01,
            jitter=False,
            retry_on=RecoverableInfrastructureError,
        ),
        error_handler=safe_node_error_handler,
    )
    builder.add_node('supervisor_route', supervisor_route_node, error_handler=safe_node_error_handler)
    builder.add_node('faculty_review', faculty_review_node, error_handler=safe_node_error_handler)
    builder.add_node('learning_agent', run_learning, error_handler=safe_node_error_handler)
    builder.add_node('safe_review', safe_review_node, error_handler=safe_node_error_handler)
    builder.add_node('supervisor_consolidation', supervisor_consolidation_node, error_handler=safe_node_error_handler)

    builder.add_edge(START, 'supervisor_dispatch')
    builder.add_edge('supervisor_dispatch', 'evaluation_agent')
    builder.add_edge('evaluation_agent', 'supervisor_route')
    builder.add_conditional_edges('supervisor_route', route_after_evaluation, {
        'assignment_mismatch': 'faculty_review',
        'missing_evidence': 'faculty_review',
        'syntax_failure': 'learning_agent',
        'test_failure': 'learning_agent',
        'evidence_supported': 'learning_agent',
        'workflow_error': 'safe_review',
    })
    builder.add_conditional_edges('faculty_review', route_after_faculty_review, {
        'learning_agent': 'learning_agent',
    })
    builder.add_edge('learning_agent', 'supervisor_consolidation')
    builder.add_edge('safe_review', 'supervisor_consolidation')
    builder.add_edge('supervisor_consolidation', END)
    return builder.compile()


evaluation_graph = build_evaluation_graph()
