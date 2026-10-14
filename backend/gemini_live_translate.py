"""Gemini Live Translate adapter with local thainp-tts output.

Gemini supplies the realtime source and Vietnamese transcripts. Gemini's
generated audio is intentionally ignored so the selected local thainp-tts
voice remains the only translated voice heard by the user.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable

from google import genai
from google.genai import types


class GeminiLiveError(RuntimeError):
    """Raised when the Gemini Live Translate session cannot continue."""


EventCallback = Callable[[dict], Awaitable[None]]


def gemini_live_status() -> dict[str, str | bool]:
    return {
        "provider": "gemini_live",
        "enabled": True,
        "configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "model": os.getenv(
            "GEMINI_LIVE_MODEL", "gemini-3.5-live-translate-preview"
        ),
        "target_lang": os.getenv("GEMINI_LIVE_TARGET_LANGUAGE", "vi"),
        "endpoint": "Gemini Live API",
        "tts": "thainp-tts-local",
    }


def _append_delta(current: str, delta: str, source_lang: str) -> str:
    """Merge Gemini's incremental or cumulative transcription updates.

    Live transcription events are not guaranteed to be clean append-only
    deltas: a later event can repeat the end of the previous event, or contain
    the whole turn again. Blind concatenation therefore makes the source and
    translation look duplicated in the UI.
    """

    clean = " ".join(str(delta or "").split()).strip()
    if not clean:
        return current
    if not current:
        return clean

    current = " ".join(str(current or "").split()).strip()
    if clean == current:
        return current

    # Gemini may add terminal punctuation when revising a cumulative update.
    current_prefix = current.rstrip(" .,!?:;。！？、，：；…")
    clean_prefix = clean.rstrip(" .,!?:;。！？、，：；…")
    if clean.startswith(current) or clean.startswith(current_prefix):
        return clean
    if current.startswith(clean) or current.startswith(clean_prefix):
        return current

    # A repeated complete phrase is already present in the accumulated text.
    if clean in current:
        return current
    if current in clean:
        return clean

    # Merge overlapping chunk boundaries, e.g. "...大丈夫です" +
    # "大丈夫です。よろしく".
    for overlap in range(min(len(current), len(clean)), 0, -1):
        if current[-overlap:] != clean[:overlap]:
            continue
        remainder = clean[overlap:]
        if not remainder:
            return current
        no_space_before = remainder[0] in ".,!?:;)]}。！？、，：；）】」』"
        no_space_language = source_lang in {"ja", "zh", "ko"}
        separator = "" if no_space_language or no_space_before else " "
        return f"{current}{separator}{remainder}".strip()

    separator = "" if source_lang in {"ja", "zh", "ko"} else " "
    return f"{current}{separator}{clean}".strip()


class GeminiLiveTranslateStream:
    """Maintain one bidirectional Gemini Live Translate audio session."""

    def __init__(self, source_lang: str) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.source_lang = (source_lang or "auto").strip().lower()
        self.model = os.getenv(
            "GEMINI_LIVE_MODEL", "gemini-3.5-live-translate-preview"
        ).strip()
        self.target_language = os.getenv(
            "GEMINI_LIVE_TARGET_LANGUAGE", "vi"
        ).strip()
        self.echo_target_language = os.getenv(
            "GEMINI_LIVE_ECHO_TARGET_LANGUAGE", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.session = None
        self.sender_task: asyncio.Task | None = None
        self._input_text = ""
        self._output_text = ""
        self._turn_order = 0

    def _config(self) -> types.LiveConnectConfig:
        input_transcription = types.AudioTranscriptionConfig()
        if self.source_lang not in {"", "auto"}:
            input_transcription.language_codes = [self.source_lang]
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            input_audio_transcription=input_transcription,
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=self.target_language,
                echo_target_language=self.echo_target_language,
            ),
        )

    async def run(
        self,
        audio_queue: asyncio.Queue[bytes | None],
        on_event: EventCallback,
    ) -> None:
        if not self.api_key:
            raise GeminiLiveError(
                "Chưa cấu hình GEMINI_API_KEY trong backend/.env."
            )
        client = genai.Client(api_key=self.api_key)
        try:
            async with client.aio.live.connect(
                model=self.model,
                config=self._config(),
            ) as session:
                self.session = session
                self.sender_task = asyncio.create_task(
                    self._send_audio(session, audio_queue)
                )
                await on_event({
                    "type": "status",
                    "status": "gemini_live_ready",
                    "message": (
                        f"Đã kết nối Gemini Live Translate ({self.model}); "
                        "đang dùng thainp-tts local."
                    ),
                })
                async for response in session.receive():
                    await self._handle_response(response, on_event)
        except GeminiLiveError:
            raise
        except Exception as exc:
            raise GeminiLiveError(f"Gemini Live kết nối lỗi: {exc}") from exc
        finally:
            if self.sender_task:
                self.sender_task.cancel()
                await asyncio.gather(self.sender_task, return_exceptions=True)
                self.sender_task = None
            self.session = None
            try:
                client.close()
            except Exception:
                pass

    async def _handle_response(
        self,
        response,
        on_event: EventCallback,
    ) -> None:
        content = getattr(response, "server_content", None)
        if not content:
            return
        input_transcription = getattr(content, "input_transcription", None)
        output_transcription = getattr(content, "output_transcription", None)
        changed = False
        input_text = getattr(input_transcription, "text", "")
        output_text = getattr(output_transcription, "text", "")
        if input_text:
            self._input_text = _append_delta(
                self._input_text,
                input_text,
                self.source_lang,
            )
            changed = True
        if output_text:
            self._output_text = _append_delta(
                self._output_text,
                output_text,
                "vi",
            )
            changed = True
        if changed:
            await on_event({
                "type": "partial_transcript",
                "text": self._input_text,
                "translated_text": self._output_text,
                "turn_order": self._turn_order,
            })

        if getattr(content, "turn_complete", False):
            if self._input_text or self._output_text:
                await on_event({
                    "type": "final_transcript",
                    "text": self._input_text,
                    "translated_text": self._output_text,
                    "turn_order": self._turn_order,
                })
            self._input_text = ""
            self._output_text = ""
            self._turn_order += 1

    async def _send_audio(self, session, audio_queue: asyncio.Queue[bytes | None]) -> None:
        try:
            while True:
                chunk = await audio_queue.get()
                try:
                    if chunk is None:
                        return
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk,
                            mime_type="audio/pcm;rate=16000",
                        )
                    )
                finally:
                    audio_queue.task_done()
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        if self.sender_task:
            self.sender_task.cancel()
            await asyncio.gather(self.sender_task, return_exceptions=True)
            self.sender_task = None
        session = self.session
        if session is not None:
            close = getattr(session, "close", None)
            if close:
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass
