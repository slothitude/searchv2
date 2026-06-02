from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
from core.events import bus

router = APIRouter()


@router.get("/events/stream")
async def event_stream(request: Request, channel: str = "default"):
    return EventSourceResponse(bus.stream(channel))


@router.post("/events/publish")
async def publish_event(event: str, data: str = "{}", channel: str = "default"):
    import json
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        parsed = {"raw": data}
    await bus.publish(event, parsed, channel)
    return {"published": True, "event": event, "channel": channel}
