"""Standalone transcription subprocess — runs faster-whisper and outputs JSON to stdout."""
import json
import sys


def main():
    audio_path = sys.argv[1]
    model_name = sys.argv[2]

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments_iter, _ = model.transcribe(
        audio_path,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segments = [
        {"start": s.start, "end": s.end, "text": s.text.strip()}
        for s in segments_iter
    ]
    json.dump(segments, sys.stdout)


if __name__ == "__main__":
    main()
