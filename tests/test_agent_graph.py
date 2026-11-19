import unittest

from agents import evaluation_agent
from agents.graph import (
    MAX_INFRASTRUCTURE_ATTEMPTS,
    RecoverableInfrastructureError,
    build_evaluation_graph,
    evaluation_graph,
    select_route,
)
from agents.graph_state import EvaluationGraphState, make_initial_state


def state_for(source, description='Sum even values from a list.'):
    return make_initial_state(
        submission_id=91,
        student_id=7,
        student_name='Test Learner',
        assignment_id=12,
        assignment_title='Even Sum',
        assignment_description=description,
        assignment_rubric={'Correctness': 5, 'Code quality': 1},
        source_code=source,
        configured_test_case_count=3,
        approved_learning_history={},
        evidence_topics=['list aggregation and filtering'],
    )


class LangGraphWorkflowTests(unittest.TestCase):
    def test_graph_compiles_with_three_agents_and_expected_nodes(self):
        nodes=set(evaluation_graph.get_graph().nodes)
        self.assertTrue({
            '__start__','supervisor_dispatch','evaluation_agent','supervisor_route',
            'faculty_review','learning_agent','safe_review','supervisor_consolidation','__end__',
        }.issubset(nodes))
        self.assertEqual(MAX_INFRASTRUCTURE_ATTEMPTS,2)

    def test_state_contains_no_secrets_or_hidden_test_values(self):
        fields=EvaluationGraphState.__annotations__
        self.assertNotIn('api_key',fields)
        self.assertNotIn('hidden_test_inputs',fields)
        self.assertNotIn('expected_outputs',fields)
        state=state_for('def even_sum(values): return 0')
        self.assertNotIn('test_cases',state)
        self.assertNotIn('hidden_test_inputs',state)

    def test_valid_source_runs_agents_but_never_claims_execution_or_correctness(self):
        result=evaluation_graph.invoke(state_for(
            'def even_sum(values):\n    return sum(v for v in values if v % 2 == 0)\n'
        ))
        self.assertEqual(result['execution_status'],'NOT RUN — secure sandbox unavailable')
        self.assertEqual(result['test_results'],[])
        self.assertEqual(result['proposed_marks'],0)
        self.assertEqual(result['route_decision'],'missing_evidence')
        self.assertTrue(result['faculty_review_required'])
        self.assertEqual(result['faculty_review_status'],'Pending')
        self.assertTrue(all(row['status']=='NOT EVALUATED' for row in result['rubric_breakdown']))
        agents={entry['agent'] for entry in result['decisions']}
        self.assertEqual(agents,{'Supervisor','Evaluation Agent','Learning Agent'})
        self.assertIn('consolidate_pending_report',[entry['action'] for entry in result['decisions']])

    def test_mismatch_routes_to_faculty_review_and_suppresses_irrelevant_quality(self):
        result=evaluation_graph.invoke(state_for(
            'from flask import Flask\napp=Flask(__name__)\n@app.get("/")\ndef home(): return "ok"\n'
        ))
        self.assertEqual(result['route_decision'],'assignment_mismatch')
        self.assertTrue(result['faculty_review_required'])
        self.assertEqual(result['quality_observations'],[])
        self.assertEqual(result['proposed_marks'],0)
        self.assertTrue(any(entry['action']=='request_faculty_review' for entry in result['decisions']))
        self.assertNotIn('docstring',str(result['learning_feedback']).lower())

    def test_syntax_failure_routes_to_learning_then_pending_report(self):
        result=evaluation_graph.invoke(state_for('def even_sum(values)\n    return 0\n'))
        self.assertEqual(result['route_decision'],'syntax_failure')
        self.assertTrue(result['learning_feedback'])
        self.assertEqual(result['faculty_review_status'],'Pending')
        self.assertEqual(result['proposed_marks'],0)

    def test_verified_test_failure_is_a_conditional_learning_route(self):
        state=state_for('def even_sum(values): return 0')
        state.update({
            'compilation_status':'Valid Python syntax',
            'execution_status':'COMPLETED in isolated sandbox',
            'test_results':[{'verified':True,'source':'sandbox','passed':False,'label':'edge'}],
        })
        route,reason=select_route(state)
        self.assertEqual(route,'test_failure')
        self.assertIn('Verified sandbox evidence',reason)

    def test_unexpected_evaluation_error_becomes_safe_review_without_retry(self):
        calls={'count':0}
        def broken_evaluator(agent_state, description, test_cases=None):
            calls['count']+=1
            raise ValueError('static evaluator defect')
        graph=build_evaluation_graph(evaluator=broken_evaluator)
        result=graph.invoke(state_for('def even_sum(values): return 0'))
        self.assertEqual(calls['count'],1)
        self.assertEqual(result['error_info']['type'],'ValueError')
        self.assertEqual(result['evaluation_status'],'Needs review')
        self.assertEqual(result['faculty_review_status'],'Pending')
        self.assertEqual(result['proposed_marks'],0)

    def test_only_recoverable_infrastructure_errors_are_retried_and_bounded(self):
        calls={'count':0}
        def transient_then_evaluate(agent_state, description, test_cases=None):
            calls['count']+=1
            if calls['count']==1:
                raise RecoverableInfrastructureError('temporary evaluator service issue')
            return evaluation_agent.evaluate(agent_state,description,test_cases)
        graph=build_evaluation_graph(evaluator=transient_then_evaluate)
        result=graph.invoke(state_for('def even_sum(values): return 0'))
        self.assertEqual(calls['count'],2)
        self.assertIsNone(result['error_info'])
        self.assertEqual(result['faculty_review_status'],'Pending')


if __name__=='__main__':
    unittest.main()
