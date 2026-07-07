from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import red_alert

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None


@router.post("/red-alert", response_class=HTMLResponse)
async def activate_red_alert(request: Request):
    await red_alert.activate()
    return templates.TemplateResponse(
        request, "partials/red_alert_btn.html", {"active": True}
    )


@router.post("/red-alert/cancel", response_class=HTMLResponse)
async def deactivate_red_alert(request: Request):
    await red_alert.deactivate()
    return templates.TemplateResponse(
        request, "partials/red_alert_btn.html", {"active": False}
    )
