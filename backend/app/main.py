from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import CONFIG
from app.api.chat import router as chat_router

app = FastAPI(title=CONFIG.app_name, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "app": CONFIG.app_name, "search_provider": "firecrawl_mcp", "rest_fallback": False}

app.include_router(chat_router)
