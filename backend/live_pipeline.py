"""Realtime Gemini translation pipeline with local thainp-tts speech output."""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass

from fastapi import WebSocket

try:
    from .gemini_live_translate import (
        GeminiLiveError,
        GeminiLiveTranslateStream,
    )
    from .sentence_splitter import split_complete_sentences
    from .tts_engine import TTSService
except ImportError:  # Supports uvicorn server:app from the backend directory.
    from gemini_live_translate import (
        GeminiLiveError,
        GeminiLiveTranslateStream,
    )
    from sentence_splitter import split_complete_sentences
    from tts_engine import TTSService


@dataclass
class PipelineSettings:
    source_lang: str = "auto"
    voice_id: str = "Ngọc Huyền 2.0"
    style: str = "tu_nhien"
    tts_speed: float = 1.0


@dataclass
class TurnState:
    source_text: str = ""
    live_tts_words_emitted: int = 0


_TTS_FILLER_WORDS = frozenset({
    "a",
    "à",
    "ờ",
    "ờm",
    "ừ",
    "ừm",
    "hả",
    "hử",
    "hừm",
    "hmm",
    "uh",
    "um",
    "mhm",
})
_TTS_FILLER_PATTERN = re.compile(
    r"(?<!\w)(?:" + "|".join(sorted(_TTS_FILLER_WORDS, key=len, reverse=True)) + r")(?!\w)",
    flags=re.IGNORECASE | re.UNICODE,
)


def _sanitize_tts_text(text: str) -> str:
    """Remove conversational fillers before sending text to the TTS model."""

    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return ""
    clean = _TTS_FILLER_PATTERN.sub("", clean)
    clean = re.sub(r"\s+([,.;:!?…。！？、])", r"\1", clean)
    clean = re.sub(r"([,;:])\s*(?=[,.;:!?…。！？、]|$)", "", clean)
    clean = re.sub(r"(^|[\(\[\{“‘])\s*[,;:]+\s*", r"\1", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    clean = clean.strip(" ,;:")
    return clean if re.search(r"[^\W_]", clean, flags=re.UNICODE) else ""


def _should_skip_tts(text: str) -> bool:
    """Return whether a transcript contains only conversational fillers."""

    enabled = os.getenv("TTS_SKIP_FILLERS", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    tokens = re.findall(r"[^\W_]+", str(text or "").casefold(), flags=re.UNICODE)
    return not tokens or (
        len(tokens) <= 4 and all(token in _TTS_FILLER_WORDS for token in tokens)
    )


def _is_filler_token(token: str) -> bool:
    """Return whether a transcript token is a standalone conversational filler."""

    normalized = re.sub(
        r"^[^\w]+|[^\w]+$",
        "",
        str(token or "").casefold(),
        flags=re.UNICODE,
    )
    return normalized in _TTS_FILLER_WORDS


def _content_word_count(words: list[str]) -> int:
    """Count words for the realtime TTS threshold, excluding filler tokens."""

    return sum(not _is_filler_token(word) for word in words)


def _raw_end_for_content_words(words: list[str], limit: int) -> int:
    """Return a raw-token end index containing up to ``limit`` content words."""

    content_words = 0
    for index, word in enumerate(words, start=1):
        if not _is_filler_token(word):
            content_words += 1
        if content_words >= limit:
            return index
    return len(words)


class LiveSession:
    def __init__(self, websocket: WebSocket, settings: PipelineSettings) -> None:
        self.websocket = websocket
        self.settings = settings
        self.audio_stream = GeminiLiveTranslateStream(settings.source_lang)
        self.tts = TTSService()
        self.audio_jobs: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)
        self.text_jobs: asyncio.Queue[tuple[str, float, str | None]] = asyncio.Queue(
            maxsize=16
        )
        self.events: asyncio.Queue[tuple[dict, bytes | None]] = asyncio.Queue()
        self.stream_task: asyncio.Task | None = None
        self.text_worker: asyncio.Task | None = None
        self.writer: asyncio.Task | None = None
        self.turn_states: dict[int, TurnState] = {}

    @staticmethod
    def parse_speed(value: object) -> float:
        try:
            speed = float(value)
        except (TypeError, ValueError):
            speed = 1.0
        return max(0.5, min(2.0, speed))

    def update_tts_speed(self, value: object) -> None:
        self.settings.tts_speed = self.parse_speed(value)

    async def start(self) -> None:
        self.text_worker = asyncio.create_task(self._text_process_loop())
        self.writer = asyncio.create_task(self._write_loop())
        self.stream_task = asyncio.create_task(self._stream_loop())
        await self.websocket.send_json({
            "type": "ready",
            "source_lang": self.settings.source_lang,
            "target_lang": "vi",
            "asr_provider": "gemini_live",
            "translation_provider": "gemini_live",
            "tts_provider": "thainp-tts-local",
        })

    def push_audio(self, pcm16: bytes) -> None:
        if not pcm16:
            return
        try:
            self.audio_jobs.put_nowait(pcm16)
        except asyncio.QueueFull:
            try:
                self.audio_jobs.get_nowait()
                self.audio_jobs.task_done()
            except asyncio.QueueEmpty:
                pass
            self.audio_jobs.put_nowait(pcm16)

    async def close(self) -> None:
        await self.audio_stream.stop()
        if self.stream_task:
            try:
                await asyncio.wait_for(self.stream_task, timeout=3)
            except asyncio.TimeoutError:
                self.stream_task.cancel()
                await asyncio.gather(self.stream_task, return_exceptions=True)
            self.stream_task = None
        self.turn_states.clear()
        try:
            await asyncio.wait_for(self.text_jobs.join(), timeout=8)
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.wait_for(self.events.join(), timeout=3)
        except asyncio.TimeoutError:
            pass
        for task in (self.text_worker, self.writer):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self.text_worker, self.writer) if task),
            return_exceptions=True,
        )

    async def _stream_loop(self) -> None:
        try:
            await self.audio_stream.run(self.audio_jobs, self._on_transcript_event)
        except GeminiLiveError as exc:
            await self.events.put(({"type": "error", "message": str(exc)}, None))
        except Exception as exc:
            await self.events.put(({"type": "error", "message": str(exc)}, None))

    async def _on_transcript_event(self, payload: dict) -> None:
        event_type = payload.get("type")
        turn_order = int(payload.get("turn_order", -1))
        if event_type == "partial_transcript":
            await self._handle_live_partial(
                turn_order,
                str(payload.get("text") or "").strip(),
                str(payload.get("translated_text") or "").strip(),
            )
            return
        if event_type == "final_transcript":
            await self._handle_live_final(
                turn_order,
                str(payload.get("text") or "").strip(),
                str(payload.get("translated_text") or "").strip(),
            )
            return
        await self.events.put((payload, None))

    async def _handle_live_partial(
        self,
        turn_order: int,
        source_text: str,
        translated_text: str,
    ) -> None:
        if source_text or translated_text:
            await self.events.put(({
                "type": "partial",
                "source_text": source_text,
                "text_vi_partial": translated_text,
            }, None))
        if not translated_text:
            return
        state = self.turn_states.setdefault(turn_order, TurnState())
        state.source_text = source_text or state.source_text

        # Gemini Live already provides a translated sentence boundary. Speak
        # each completed Vietnamese sentence immediately instead of waiting
        # for turn_complete, because the speaker may continue with the next
        # sentence without pausing.
        self._queue_live_tts_progress(
            state,
            translated_text,
            source_text=state.source_text,
            flush_tail=False,
        )

    async def _handle_live_final(
        self,
        turn_order: int,
        source_text: str,
        translated_text: str,
    ) -> None:
        if not translated_text:
            return
        state = self.turn_states.setdefault(turn_order, TurnState())
        state.source_text = source_text or state.source_text
        self._queue_live_tts_progress(
            state,
            translated_text,
            source_text=state.source_text,
            flush_tail=True,
        )
        self.turn_states.pop(turn_order, None)

    def _queue_live_tts_progress(
        self,
        state: TurnState,
        translated_text: str,
        *,
        source_text: str = "",
        flush_tail: bool,
    ) -> None:
        """Queue complete sentences or a 10-content-word fallback chunk."""

        clean = " ".join(str(translated_text or "").split()).strip()
        if not clean:
            return
        words = clean.split()
        complete, _tail = split_complete_sentences(clean)
        complete_word_count = len(" ".join(complete).split())
        consumed = min(state.live_tts_words_emitted, len(words))

        def queue_segment(segment_words: list[str]) -> None:
            segment = " ".join(segment_words).strip()
            if not segment or not re.search(r"[^\W_]", segment, flags=re.UNICODE):
                return
            self._queue_text(
                segment,
                source_text=source_text or segment,
                translated_text=segment,
            )

        if complete_word_count > consumed:
            queue_segment(words[consumed:complete_word_count])
            consumed = complete_word_count

        tail_words = words[consumed:]
        if flush_tail:
            queue_segment(tail_words)
            consumed = len(words)
        else:
            minimum_words = max(1, int(os.getenv("TTS_MIN_WORDS", "10")))
            chunk_words = max(
                minimum_words,
                int(os.getenv("TTS_CHUNK_WORDS", "10")),
            )
            if _content_word_count(tail_words) >= minimum_words:
                chunk_end = _raw_end_for_content_words(tail_words, chunk_words)
                chunk = tail_words[:chunk_end]
                queue_segment(chunk)
                consumed += chunk_end

        state.live_tts_words_emitted = consumed

    def _queue_text(
        self,
        text: str,
        *,
        source_text: str | None = None,
        translated_text: str | None = None,
    ) -> None:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return
        source = " ".join(str(source_text or clean).split()).strip()
        item = (source, time.perf_counter(), translated_text)
        try:
            self.text_jobs.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self.text_jobs.get_nowait()
                self.text_jobs.task_done()
            except asyncio.QueueEmpty:
                pass
            self.text_jobs.put_nowait(item)

    async def _text_process_loop(self) -> None:
        while True:
            source_text, started, translated = await self.text_jobs.get()
            try:
                if translated is None:
                    raise RuntimeError("Gemini Live không trả về bản dịch tiếng Việt.")
                tts_text = _sanitize_tts_text(translated)
                if not tts_text or _should_skip_tts(tts_text):
                    await self.events.put(({
                        "type": "translation",
                        "source_text": source_text,
                        "text_vi": translated,
                        "tts_skipped": True,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                    }, None))
                    continue
                wav = await asyncio.to_thread(
                    self.tts.synthesize_wav,
                    tts_text,
                    self.settings.voice_id,
                    self.settings.style,
                    self.settings.tts_speed,
                )
                await self.events.put(({
                    "type": "translation",
                    "source_text": source_text,
                    "text_vi": translated,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }, wav))
            except Exception as exc:
                await self.events.put(({"type": "error", "message": str(exc)}, None))
            finally:
                self.text_jobs.task_done()

    async def _write_loop(self) -> None:
        while True:
            event, wav = await self.events.get()
            try:
                await self.websocket.send_json(event)
                if wav:
                    await self.websocket.send_bytes(wav)
            finally:
                self.events.task_done()
