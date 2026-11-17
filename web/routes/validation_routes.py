"""Read-only forward validation; viewing this page never runs the strategy."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from .. import auth, db, state
from ..templating import templates
from trading.forward_validation import report

router = APIRouter()


def snapshot():
    events, truncated, error = db.load_validation_events()
    portfolio = state.portfolio()
    result = report(events, truncated=truncated, pending=len(portfolio.validation_pending))
    result["capture"] = {k: v for k, v in portfolio.validation_state.items() if k != "seen"}
    result["storage_error"] = error
    return result


@router.get("/validation")
def validation_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request=request, name="validation.html", context={"request": request,
        "active": "validation", "user": auth.current_user(request), "result": snapshot()})


@router.get("/api/validation")
def validation_api(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    return snapshot()
