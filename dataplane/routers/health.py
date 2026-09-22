from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check — used by Phase 0's Docker/CI wiring and, in Phase 1,
    as the first endpoint proven to work behind JWT auth before any real
    feature is built (architecture plan §8, Phase 1)."""
    return {"status": "ok"}
