"""Modal serverless Whisper transcription endpoint.

Deploy:
    pip install modal
    modal setup
    modal deploy modal_whisper.py

Then set MODAL_WHISPER_URL in your .env to the printed endpoint URL.
"""
import modal
from fastapi import UploadFile

app = modal.App("echo360-whisper")

MODEL_NAME = "large-v3-turbo"
MODEL_DIR = "/cache/whisper"


def _download_model():
    """Download the whisper model at image build time so it's baked into the layer."""
    from faster_whisper import WhisperModel

    WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", download_root=MODEL_DIR)


image = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-runtime-ubuntu24.04", add_python="3.12")
    .apt_install("ffmpeg")
    .pip_install("faster-whisper", "fastapi[standard]")
    .run_function(_download_model)
)


@app.cls(image=image, gpu="L4", scaledown_window=120, timeout=1800)
class Whisper:
    @modal.enter()
    def load_model(self):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            MODEL_NAME, device="cuda", compute_type="float16", download_root=MODEL_DIR
        )

    @modal.fastapi_endpoint(method="POST", docs=True)
    async def transcribe(self, file: UploadFile):
        import os
        import tempfile

        content = await file.read()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(content)
            tmp_path = f.name

        try:
            segments_iter, _ = self.model.transcribe(
                tmp_path,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            segments = [
                {
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "text": s.text.strip(),
                }
                for s in segments_iter
            ]
        finally:
            os.unlink(tmp_path)

        return {"segments": segments}
