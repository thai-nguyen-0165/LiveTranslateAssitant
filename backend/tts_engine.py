"""thainp-tts v3 Turbo adapter extracted from Auto-Translade-video."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
VOICES_FILE = ROOT / "src" / "vieneu" / "assets" / "voices_v3_turbo.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TTSService:
    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.RLock()
        # The default remains the upstream model ID; thainp-tts is our product
        # name, not a replacement for the upstream Hugging Face identifier.
        self.model_repo = os.getenv("TTS_MODEL_REPO", "pnnbao-ump/VieNeu-TTS-v3-Turbo")
        self.codec_repo = os.getenv(
            "TTS_CODEC_REPO", "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX"
        )
        self.precision = os.getenv("TTS_PRECISION", "int8")
        self.voice = os.getenv("TTS_VOICE", "Ngọc Huyền 2.0")

    def _get_engine(self):
        with self._lock:
            if self._engine is None:
                from src.vieneu import Vieneu

                self._engine = Vieneu(
                    mode="v3turbo",
                    backend="onnx",
                    backbone_repo=self.model_repo,
                    codec_repo=self.codec_repo,
                    precision=self.precision,
                    threads=int(os.getenv("TTS_THREADS", "0")),
                )
            return self._engine

    def voices(self) -> list[dict]:
        payload = json.loads(VOICES_FILE.read_text(encoding="utf-8"))
        presets = payload.get("presets", {})
        return [
            {
                "voice_id": name,
                "name": name,
                "description": value.get("description", ""),
                "gender": value.get("gender", ""),
            }
            for name, value in presets.items()
        ]

    @staticmethod
    def _atempo_filter(speed: float) -> str:
        if speed <= 0:
            raise ValueError("TTS speed must be positive")
        factors: list[float] = []
        remaining = speed
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
        factors.append(remaining)
        return ",".join(f"atempo={factor:.6g}" for factor in factors)

    @classmethod
    def _apply_speed(cls, audio: bytes, speed: float) -> bytes:
        """Change tempo while keeping the generated voice pitch unchanged."""

        if abs(speed - 1.0) < 0.001:
            return audio
        try:
            source_wav = wave.open(io.BytesIO(audio), "rb")
            channels = source_wav.getnchannels()
            sample_rate = source_wav.getframerate()
            source_wav.close()
        except (wave.Error, EOFError) as exc:
            raise RuntimeError("TTS trả về WAV không hợp lệ") from exc
        ffmpeg = os.getenv("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
        if not shutil.which(ffmpeg) and not os.path.isfile(ffmpeg):
            raise RuntimeError(
                "Không tìm thấy ffmpeg để điều chỉnh tốc độ TTS. "
                "Hãy cài ffmpeg hoặc cấu hình FFMPEG_BIN trong backend/.env."
            )
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                "pipe:0",
                "-filter:a",
                cls._atempo_filter(speed),
                "-ac",
                str(channels),
                "-ar",
                str(sample_rate),
                "-acodec",
                "pcm_s16le",
                "-f",
                "s16le",
                "pipe:1",
            ],
            input=audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            detail = result.stderr.decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Không thể điều chỉnh tốc độ TTS: {detail}")
        output = io.BytesIO()
        with wave.open(output, "wb") as output_wav:
            output_wav.setnchannels(channels)
            output_wav.setsampwidth(2)
            output_wav.setframerate(sample_rate)
            output_wav.writeframes(result.stdout)
        return output.getvalue()

    def synthesize_wav(
        self,
        text: str,
        voice: str | None = None,
        style: str = "tu_nhien",
        speed: float = 1.0,
    ) -> bytes:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            raise ValueError("TTS text is empty")
        with self._lock:
            engine = self._get_engine()
            audio = engine.infer(
                clean,
                voice=voice or self.voice,
                style=style,
                max_chars=min(96, int(os.getenv("TTS_MAX_CHARS", "96"))),
                temperature=float(os.getenv("TTS_TEMPERATURE", "0.68")),
                top_k=int(os.getenv("TTS_TOP_K", "20")),
                top_p=float(os.getenv("TTS_TOP_P", "0.90")),
                repetition_penalty=1.22,
                apply_watermark=True,
            )
        samples = np.asarray(audio, dtype=np.float32)
        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767.0).astype("<i2").tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(getattr(engine, "sample_rate", 48000)))
            wav.writeframes(pcm)
        return self._apply_speed(output.getvalue(), max(0.5, min(2.0, float(speed))))

    def status(self) -> dict[str, str | bool]:
        return {
            "loaded": self._engine is not None,
            "model": self.model_repo,
            "precision": self.precision,
            "voice": self.voice,
        }
