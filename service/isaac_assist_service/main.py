import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load all local configurations from .env
load_dotenv()

# We import the routers after env load just to be safe
from .chat.routes import router as chat_router
from .fingerprint.routes import router as fingerprint_router
from .snapshots.routes import router as snapshot_router
from .retrieval.routes import router as retrieval_router
from .analysis.routes import router as analysis_router
from .planner.routes import router as planner_router
from .governance.routes import router as governance_router
from .settings.routes import router as settings_router
from .finetune.routes import router as finetune_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Isaac Assist Service",
    description="Background service governing LLM inference, Stage Analysis, and Validation for Omniverse.",
    version="1.0.0"
)

# Allow the Extension UI or external Web Apps to hit this service cleanly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For local testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Module Endpoints
app.include_router(chat_router, prefix="/api/v1/chat", tags=["Chat Orchestration"])
app.include_router(fingerprint_router, prefix="/api/v1/fingerprint", tags=["Environment Fingerprint"])
app.include_router(snapshot_router, prefix="/api/v1/snapshots", tags=["Snapshot Manager"])
app.include_router(retrieval_router, prefix="/api/v1/retrieval", tags=["Source Registry RAG"])
app.include_router(analysis_router, prefix="/api/v1/analysis", tags=["Stage Analyzer"])
app.include_router(planner_router, prefix="/api/v1/plans", tags=["Patch Planner"])
app.include_router(governance_router, prefix="/api/v1/governance", tags=["Approval Engine"])
app.include_router(settings_router, prefix="/api/v1/settings", tags=["Configuration Options"])
app.include_router(finetune_router, prefix="/api/v1/finetune", tags=["Fine-tuning Builder"])

@app.get("/health")
async def health_check():
    from .config import config
    return {
        "status": "ok",
        "service": "isaac-assist-backend",
        "llm_mode": config.llm_mode,
        "model": config.cloud_model_name if config.llm_mode != "local" else config.local_model_name,
    }

if __name__ == "__main__":
    import argparse
    import uvicorn

    VALID_MODES = ["local", "cloud", "anthropic", "openai", "grok"]

    parser = argparse.ArgumentParser(description="Isaac Assist Service")
    parser.add_argument(
        "--mode", "-m",
        choices=VALID_MODES,
        default=None,
        help="LLM provider mode (overrides LLM_MODE in .env)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", default=True)
    args = parser.parse_args()

    if args.mode:
        import os
        os.environ["LLM_MODE"] = args.mode
        # Re-init config so the provider factory picks it up
        from .config import config as _cfg
        _cfg.llm_mode = args.mode

    uvicorn.run("service.isaac_assist_service.main:app", host=args.host, port=args.port, reload=args.reload)
