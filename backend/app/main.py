"""FastAPI application entry point.

Sets up the app metadata, CORS for local frontend development, and the
health endpoint. Feature routers (e.g. the review API) are registered here
as they are implemented. See CLAUDE.md §5 and §6.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.review_routes import router as review_router

app = FastAPI(
    title="AI-Powered GitHub Pull Request Reviewer",
    description=(
        "Submit a public GitHub Pull Request URL and receive an AI-generated, "
        "first-level code review. Findings are suggestions and must be verified "
        "by a human reviewer."
    ),
    version="0.1.0",
)

# CORS for local development. The Vite dev server runs on localhost:5173.
# See CLAUDE.md §12 — allow the dev origin rather than a blanket wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health_check():
    """Liveness check — confirms the backend is running."""
    return {"status": "ok"}


# Feature routers.
app.include_router(review_router)
