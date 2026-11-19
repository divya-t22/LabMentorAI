import ast, io, tokenize

def analyze(source: str) -> dict:
    result = {'syntax_ok': True, 'syntax_error': '', 'issues': [], 'fingerprint': []}
    if not source.strip():
        result.update(syntax_ok=False, syntax_error='Submission is empty.')
        return result
    try: tree = ast.parse(source)
    except SyntaxError as exc:
        result.update(syntax_ok=False, syntax_error=f'Syntax error on line {exc.lineno}: {exc.msg}')
        return result
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not ast.get_docstring(node): result['issues'].append(f"Function '{node.name}' has no docstring.")
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and getattr(node.iter.func, 'id', '') == 'range':
            if len(node.iter.args) == 1 and isinstance(node.iter.args[0], ast.Constant) and node.iter.args[0].value > 10000:
                result['issues'].append('Large fixed iteration range may be inefficient.')
    try:
        toks = tokenize.generate_tokens(io.StringIO(source).readline)
        result['fingerprint'] = [t.string for t in toks if t.type in (tokenize.NAME, tokenize.NUMBER, tokenize.STRING, tokenize.OP)]
    except tokenize.TokenError: pass
    return result
