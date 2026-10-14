"""FastAPI entrypoint for LiveTrans."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

try:
    from .gemini_live_translate import gemini_live_status
    from .live_pipeline import LiveSession, PipelineSettings
    from .tts_engine import TTSService
except ImportError:  # Supports `uvicorn server:app` from the backend directory.
    from gemini_live_translate import gemini_live_status
    from live_pipeline import LiveSession, PipelineSettings
    from tts_engine import TTSService


load_dotenv(Path(__file__).with_name(".env"))
app = FastAPI(title="LiveTrans local backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
tts = TTSService()


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice_id: str = "Ngọc Huyền 2.0"
    style: str = "tu_nhien"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@app.get("/health")
async def health() -> dict:
    live_translation = gemini_live_status()
    asr = {
        "provider": "gemini_live",
        "configured": live_translation["configured"],
        "model": live_translation["model"],
        "language_codes": "auto",
    }
    return {
        "ok": True,
        "service": "livetrans",
        "asr": asr,
        "tts": tts.status(),
        "translation": live_translation,
        "translation_configured": bool(live_translation["configured"]),
    }


@app.get("/api/voices")
async def voices() -> dict:
    return {"provider": "thainp-tts-v3-turbo", "voices": tts.voices()}


@app.post("/api/tts/live")
async def tts_live(payload: TTSRequest) -> Response:
    audio = await asyncio.to_thread(
        tts.synthesize_wav,
        payload.text,
        payload.voice_id,
        payload.style,
        payload.speed,
    )
    return Response(content=audio, media_type="audio/wav")


@app.websocket("/ws/live")
async def live(websocket: WebSocket) -> None:
    await websocket.accept()
    query = websocket.query_params
    settings = PipelineSettings(
        source_lang=query.get("source_lang", "auto"),
        voice_id=query.get("voice_id", "Ngọc Huyền 2.0"),
        style=query.get("style", "tu_nhien"),
        tts_speed=LiveSession.parse_speed(query.get("tts_speed", "1")),
    )
    session = LiveSession(websocket, settings)
    await session.start()
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                session.push_audio(data)
            else:
                text = message.get("text") or ""
                if text == "stop":
                    break
                try:
                    control = json.loads(text)
                except json.JSONDecodeError:
                    control = {}
                if control.get("type") == "settings":
                    session.update_tts_speed(control.get("tts_speed"))
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
