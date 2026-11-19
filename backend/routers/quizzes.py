from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from routers._common import AccountDep
from quiz import store

router = APIRouter()

@router.get('/api/quiz/capabilities')
def capabilities():
    return {'answer_modes': ['notify_only', 'ai_suggest_confirm', 'ai_auto']}

class Revision(BaseModel):
    expected_revision: int

class Confirmation(Revision):
    answer: str | list[str]
    idempotency_key: str = Field(min_length=1, max_length=128)

@router.get('/api/accounts/{account_id}/quizzes/pending')
def pending(account_id: str = AccountDep):
    return store.list_pending(account_id)

@router.get('/api/accounts/{account_id}/quizzes/{quiz_id}')
def detail(quiz_id: str, account_id: str = AccountDep):
    item = store.get(account_id, quiz_id)
    if not item:
        raise HTTPException(404, 'Quiz not found')
    return item

@router.post('/api/accounts/{account_id}/quizzes/{quiz_id}/dismiss')
def dismiss(quiz_id: str, body: Revision, account_id: str = AccountDep):
    if not store.dismiss(account_id, quiz_id, body.expected_revision):
        raise HTTPException(409, 'Quiz changed or was already handled')
    return {'status': 'DISMISSED'}

@router.post('/api/accounts/{account_id}/quizzes/{quiz_id}/confirm')
def confirm(quiz_id: str, body: Confirmation, account_id: str = AccountDep):
    from quiz.workflow import confirm as confirm_quiz, WorkflowError
    try:
        return confirm_quiz(account_id, quiz_id, body.answer, body.expected_revision, body.idempotency_key)
    except WorkflowError as exc:
        raise HTTPException(exc.status, exc.message) from None
