"""
app/api/routes/persona.py — Beyond Presence persona voice chat (online only).

Agents are pre-created on the BEY platform and their IDs are hardcoded here.
Each persona uses Gemini 2.5 Flash as the LLM backend via BEY's external API integration.
BEY external API ID (Google Gemini 2.5 Flash): b75b7ef6-92fc-4b8c-a727-b7a0d40448d4

Flow:
  GET  /api/persona/list   — list the 6 available personas
  POST /api/persona/call   — create a BEY call session, return the call URL (no login required)
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.config import settings

BEY_BASE = "https://api.bey.dev/v1"


def _bey_headers() -> dict[str, str]:
    return {"x-api-key": settings.BEY_API_KEY, "Content-Type": "application/json"}

router = APIRouter(prefix="/api/persona", tags=["persona"])

# ── Pre-created BEY agent IDs (created once, reused forever) ──────────────────
# Agents were created via POST /v1/agents with Gemini 2.0 Flash as the LLM backend.
# Avatar IDs chosen from GET /v1/avatars for the best fit per persona.
PERSONAS: list[dict[str, str]] = [
    {
        "id": "amina",
        "name": "Amina",
        "gender": "female",
        "style": "Warm and patient. Uses stories and real-world examples. Never rushes.",
        "avatar_id": "8c37d173-929f-4a71-9a5f-45840bb2422b",
        "agent_id": "7e475367-6f9c-48b7-8320-0aa489233ed9",
        "color": "#E8A87C",
        "icon": "flower",
    },
    {
        "id": "sara",
        "name": "Sara",
        "gender": "female",
        "style": "Structured and encouraging. Breaks everything into clear steps.",
        "avatar_id": "2bc759ab-a7e5-4b91-941d-9e42450d6546",
        "agent_id": "0f30f5b9-ac9a-4a7a-bb0e-91f73a64ad55",
        "color": "#85C1E9",
        "icon": "star",
    },
    {
        "id": "yuruo",
        "name": "Yuruo",
        "gender": "female",
        "style": "Gentle and curious. Asks questions back to help students think.",
        "avatar_id": "70b1b917-ed16-4531-bb6c-b0bdb79449b4",
        "agent_id": "b4cc94cc-a780-4a1a-aae5-2a45b58aa363",
        "color": "#A9DFBF",
        "icon": "leaf",
    },
    {
        "id": "zaid",
        "name": "Zaid",
        "gender": "male",
        "style": "Calm and clear. Explains step by step, never skips foundations.",
        "avatar_id": "1c7a7291-ee28-4800-8f34-acfbfc2d07c0",
        "agent_id": "bffbdf9d-a6e1-47a2-8290-1a31dc4ee127",
        "color": "#D2B4DE",
        "icon": "book",
    },
    {
        "id": "awais",
        "name": "Awais",
        "gender": "male",
        "style": "Energetic and motivating. Celebrates every small win.",
        "avatar_id": "2ed7477f-3961-4ce1-b331-5e4530c55a57",
        "agent_id": "6a605223-d780-4602-947f-f1bc51b29596",
        "color": "#F9E79F",
        "icon": "fire",
    },
    {
        "id": "jerome",
        "name": "Jerome",
        "gender": "male",
        "style": "Direct and precise. Gets straight to the point, no filler.",
        "avatar_id": "c57374fa-ba3d-4c2f-8fed-9f2678bdce14_v2",
        "agent_id": "ef885353-b5d2-40ed-a830-76b0f3a914f1",
        "color": "#AED6F1",
        "icon": "target",
    },
]

_PERSONA_BY_ID = {p["id"]: p for p in PERSONAS}

# Kept for backwards compatibility — no longer writes to disk
def _load_agent_cache() -> None:
    pass


# ── Request/Response models ────────────────────────────────────────────────────

class StartCallRequest(BaseModel):
    persona_id: str
    student_name: str = "Student"


class StartCallResponse(BaseModel):
    agent_id: str
    call_url: str
    persona: dict


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/list")
async def list_personas() -> dict:
    """Return the 6 persona definitions (no internal IDs)."""
    safe = [
        {k: v for k, v in p.items() if k not in ("avatar_id", "agent_id")}
        for p in PERSONAS
    ]
    return {"personas": safe}


@router.post("/call", response_model=StartCallResponse)
async def start_persona_call(body: StartCallRequest) -> StartCallResponse:
    """
    Create a BEY call session for the chosen persona.
    Uses POST /v1/calls to get a session-specific call ID — no login required.
    """
    if not settings.BEY_API_KEY:
        raise HTTPException(status_code=503, detail="Persona chat is only available in online mode.")

    persona = _PERSONA_BY_ID.get(body.persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{body.persona_id}' not found.")

    agent_id = persona["agent_id"]

    # Create a session-specific call — bey.chat/{call_id} is public, no login needed
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{BEY_BASE}/calls",
                headers=_bey_headers(),
                json={"agent_id": agent_id, "livekit_username": body.student_name},
            )
        if resp.status_code not in (200, 201):
            logger.error("BEY create call failed {}: {}", resp.status_code, resp.text)
            raise HTTPException(status_code=502, detail="Could not start persona call.")
        call_id = resp.json()["id"]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("BEY call error: {}", exc)
        raise HTTPException(status_code=502, detail="Could not reach persona service.")

    call_url = f"https://bey.chat/{call_id}"
    logger.info("BEY call created: persona={} call_id={}", body.persona_id, call_id)

    return StartCallResponse(
        agent_id=agent_id,
        call_url=call_url,
        persona={k: v for k, v in persona.items() if k not in ("avatar_id", "agent_id")},
    )
