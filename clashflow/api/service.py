from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/service/health")
async def service_health(request: Request) -> dict[str, str]:
    up = await request.app.state.kernel.probe(request.app.state.secret)
    active = request.app.state.store.get_active()
    return {"kernel": "up" if up else "down", "active_config": active or ""}
