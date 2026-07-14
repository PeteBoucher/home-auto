import json
import os

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None
templates.env.filters["fromjson"] = json.loads
templates.env.globals["firetv_enabled"] = os.getenv("FIRETV_ENABLED", "false").lower() == "true"
