"""Shared, typed-by-convention evaluation state passed between agents."""
from dataclasses import dataclass, field
from typing import Any

@dataclass
class EvaluationState:
    student_id: int
    assignment_id: int
    submission_id: int
    source_code: str
    rubric: dict[str, float]
    test_results: list[dict[str, Any]] = field(default_factory=list)
    compilation_status: str = 'Not checked'
    execution_status: str = 'Not run'
    errors: list[str] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)
    similarity_flags: list[dict] = field(default_factory=list)
    proposed_marks: float = 0
    previous_history: dict = field(default_factory=dict)
    evidence_topics: list[str] = field(default_factory=list)
    recurring_mistakes: list[str] = field(default_factory=list)
    feedback: dict = field(default_factory=dict)
    current_agent: str = 'Supervisor'
    next_action: str = 'evaluate'
    decision_count: int = 0
    status: str = 'Pending'
    faculty_approval_status: str = 'Pending'
    decisions: list[dict] = field(default_factory=list)
    rubric_results: list[dict] = field(default_factory=list)
    assignment_relevance: dict = field(default_factory=lambda: {'requires_review': False, 'evidence': []})
