"""Strict validation for AI suggestions and user-entered answers."""

def validate_answer(problem, answer):
    kind = int(problem['problemType'])
    if kind == 5:
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 10000:
            raise ValueError('INVALID_ANSWER')
        return answer.strip()
    options = [str(option['key']) for option in problem.get('options', [])]
    if kind not in (1, 2, 3) or not isinstance(answer, list) or not answer:
        raise ValueError('INVALID_ANSWER')
    if any(not isinstance(item, str) or item not in options for item in answer):
        raise ValueError('INVALID_ANSWER')
    if len(set(answer)) != len(answer):
        raise ValueError('INVALID_ANSWER')
    limit = 1 if kind == 1 else len(options) if kind == 2 else int(problem.get('pollingCount') or 1)
    if len(answer) > limit:
        raise ValueError('INVALID_ANSWER')
    return answer
