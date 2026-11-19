"""Typed, in-memory state passed between LangGraph agent nodes."""
from typing import Any, Literal, TypedDict


RouteDecision = Literal[
    'assignment_mismatch',
    'missing_evidence',
    'syntax_failure',
    'test_failure',
    'evidence_supported',
    'workflow_error',
]


class EvaluationGraphState(TypedDict, total=False):
    # Submission and assignment context. Source is transient in-memory input;
    # test inputs/expected values and secrets are deliberately not state fields.
    submission_id: int
    student_id: int
    student_name: str
    assignment_id: int
    assignment_title: str
    assignment_description: str
    assignment_rubric: dict[str, float]
    source_code: str
    configured_test_case_count: int
    approved_learning_history: dict[str, Any]
    evidence_topics: list[str]

    # Evaluation Agent output and provenance.
    compilation_status: str
    execution_status: str
    test_results: list[dict[str, Any]]
    assignment_relevance: dict[str, Any]
    quality_observations: list[str]
    evaluation_errors: list[str]
    evidence: dict[str, Any]
    rubric_breakdown: list[dict[str, Any]]
    proposed_marks: float

    # Supervisor routing, Learning Agent output, errors, and approval gate.
    route_decision: RouteDecision
    route_reason: str
    decisions: list[dict[str, str]]
    learning_feedback: dict[str, Any]
    error_info: dict[str, str] | None
    evaluation_status: str
    faculty_review_required: bool
    faculty_review_status: Literal['Pending']
    final_report: dict[str, Any]


def make_initial_state(
    *,
    submission_id: int,
    student_id: int,
    student_name: str,
    assignment_id: int,
    assignment_title: str,
    assignment_description: str,
    assignment_rubric: dict[str, float],
    source_code: str,
    configured_test_case_count: int,
    approved_learning_history: dict[str, Any],
    evidence_topics: list[str],
) -> EvaluationGraphState:
    """Build the graph input while excluding hidden test data and credentials."""
    return {
        'submission_id': submission_id,
        'student_id': student_id,
        'student_name': student_name,
        'assignment_id': assignment_id,
        'assignment_title': assignment_title,
        'assignment_description': assignment_description,
        'assignment_rubric': dict(assignment_rubric),
        'source_code': source_code,
        'configured_test_case_count': configured_test_case_count,
        'approved_learning_history': approved_learning_history,
        'evidence_topics': list(evidence_topics),
        'compilation_status': 'Not checked',
        'execution_status': 'NOT RUN — secure sandbox unavailable',
        'test_results': [],
        'assignment_relevance': {'requires_review': False, 'evidence': []},
        'quality_observations': [],
        'evaluation_errors': [],
        'evidence': {},
        'rubric_breakdown': [],
        'proposed_marks': 0.0,
        'route_decision': 'missing_evidence',
        'route_reason': 'Evaluation has not started.',
        'decisions': [],
        'learning_feedback': {},
        'error_info': None,
        'evaluation_status': 'Proposed',
        'faculty_review_required': True,
        'faculty_review_status': 'Pending',
        'final_report': {},
    }
