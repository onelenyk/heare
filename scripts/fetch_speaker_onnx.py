#!/usr/bin/env python3
"""Download a pre-exported ECAPA speaker-embedding ONNX model.

Pulls the `.onnx` file from a wespeaker Hugging Face repo into
~/.heare/speaker_model/speaker.onnx (the default speaker_id_onnx_path).
No torch/speechbrain involved — the model runs on onnxruntime, matching
the numpy Fbank frontend in src/voice/speaker/id.py.

Usage:
    uv run python -m scripts.fetch_speaker_onnx
    HEARE_SPEAKER_REPO=<hf-repo-id> uv run python -m scripts.fetch_speaker_onnx
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# wespeaker ECAPA-TDNN (VoxCeleb2), 80-dim Fbank input, 192-dim embedding.
DEFAULT_REPO = os.environ.get(
    "HEARE_SPEAKER_REPO", "Wespeaker/wespeaker-ecapa-tdnn512-LM"
)
DEST = Path.home() / ".heare" / "speaker_model" / "speaker.onnx"


def main() -> int:
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    try:
        files = api.list_repo_files(DEFAULT_REPO)
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot list {DEFAULT_REPO}: {e}", file=sys.stderr)
        print(
            "Set HEARE_SPEAKER_REPO to a HF repo that ships an .onnx ECAPA "
            "model, or place one at "
            f"{DEST} manually.",
            file=sys.stderr,
        )
        return 1

    onnx_files = [f for f in files if f.lower().endswith(".onnx")]
    if not onnx_files:
        print(
            f"error: no .onnx file in {DEFAULT_REPO} (found: {files})",
            file=sys.stderr,
        )
        return 1

    chosen = onnx_files[0]
    print(f"downloading {DEFAULT_REPO}:{chosen} ...")
    cached = hf_hub_download(repo_id=DEFAULT_REPO, filename=chosen)

    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, DEST)
    size_mb = DEST.stat().st_size / 1e6
    print(f"saved -> {DEST} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
