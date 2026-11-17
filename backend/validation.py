"""Validation responses must never reflect credentials or untrusted request bodies."""
from fastapi.responses import JSONResponse

async def validation_exception_handler(request, exc):
    return JSONResponse(status_code=422, content={'detail': 'Invalid request data'})
