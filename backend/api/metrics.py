"""Prometheus /metrics endpoint."""
from fastapi import APIRouter, Response
from metrics import export_prometheus

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics():
    """Return all metrics in Prometheus text format."""
    return Response(content=export_prometheus(), media_type="text/plain")
