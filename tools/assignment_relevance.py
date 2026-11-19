"""Conservative static relevance checks for obvious assignment/code mismatches."""
import ast


def assess_relevance(description, source):
    """Flag a Flask web app submitted for an explicit even-list-sum exercise.

    This intentionally recognizes only a narrow, high-confidence mismatch. It
    parses Python syntax but never imports or executes the submitted source.
    """
    text=(description or '').lower()
    asks_even_list_sum=('sum' in text and 'even' in text and ('list' in text or 'values' in text))
    if not asks_even_list_sum:
        return {'requires_review':False,'evidence':[]}
    try:
        tree=ast.parse(source or '')
    except (SyntaxError, ValueError, TypeError):
        return {'requires_review':False,'evidence':['Static syntax analysis could not establish the submission topic.']}

    imports=set()
    names=set()
    calls=set()
    has_iteration=False
    has_modulo_two=False
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            imports.update(alias.name.split('.')[0].lower() for alias in node.names)
        elif isinstance(node,ast.ImportFrom) and node.module:
            imports.add(node.module.split('.')[0].lower())
        elif isinstance(node,ast.Name):
            names.add(node.id.lower())
        elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            names.add(node.name.lower())
        elif isinstance(node,(ast.For,ast.AsyncFor,ast.comprehension)):
            has_iteration=True
        elif isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name):
                calls.add(node.func.id.lower())
        elif isinstance(node,ast.BinOp) and isinstance(node.op,ast.Mod):
            if isinstance(node.right,ast.Constant) and node.right.value == 2:
                has_modulo_two=True

    framework=next((name for name in ('flask','fastapi','django') if name in imports),None)
    is_framework_app=framework is not None or any(name in names for name in ('flask','fastapi','django'))
    if not is_framework_app:
        return {'requires_review':False,'evidence':[]}

    has_sum='sum' in calls or any('sum' in name for name in names)
    has_even='even' in names or has_modulo_two
    has_list=has_iteration or bool({'list','values','items'} & names)
    if has_sum and has_even and has_list:
        return {'requires_review':False,'evidence':[]}

    evidence=[f'Submitted source imports or references the {framework or "web application"} framework.']
    if any(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.lower() in ('home','index') for node in ast.walk(tree)):
        evidence.append('The AST contains web-style entry-point functions such as home/index.')
    missing=[]
    if not has_sum: missing.append('no sum operation or sum-named function was found')
    if not has_even: missing.append('no even-value identifier or modulo filter was found')
    if not has_list: missing.append('no list iteration or values/list identifier was found')
    evidence.append('Expected even-list-sum indicators were incomplete: '+', '.join(missing)+'.')
    evidence.append('The assignment description asks for a function that sums even values from a list.')
    return {'requires_review':True,'evidence':evidence}
