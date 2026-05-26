"""Run Demucs separation and save 24-bit WAV stems without torchaudio's save path."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf
from demucs.apply import BagOfModels, apply_model
from demucs.htdemucs import HTDemucs
from demucs.pretrained import get_model
from demucs.separate import load_track


def _max_allowed_segment(model) -> float:
    if isinstance(model, HTDemucs):
        return float(model.segment)
    if isinstance(model, BagOfModels):
        return float(model.max_allowed_segment)
    return float("inf")


def _write_stem(stem_tensor, output_path: Path, samplerate: int) -> None:
    stem = stem_tensor.detach().cpu().transpose(0, 1).numpy()
    if stem.size:
        peak = float(np.max(np.abs(stem)))
        if peak > 1.0:
            stem = stem / (peak * 1.01)

    sf.write(output_path, stem, samplerate, subtype="PCM_24")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a track with Demucs and save 24-bit WAV stems.")
    parser.add_argument("--input", required=True, help="Path to the input mix")
    parser.add_argument("--output-root", required=True, help="Root folder for separated stems")
    parser.add_argument("--model", required=True, help="Demucs model name")
    parser.add_argument("--device", default="cpu", help="Execution device")
    parser.add_argument("--overlap", type=float, default=0.4, help="Chunk overlap")
    parser.add_argument("--shifts", type=int, default=1, help="Equivariant stabilization shifts")
    parser.add_argument("--segment", type=int, default=7, help="Chunk size in seconds")
    parser.add_argument("--jobs", type=int, default=0, help="CPU worker count")
    args = parser.parse_args()

    model = get_model(name=args.model)
    max_allowed_segment = _max_allowed_segment(model)
    if args.segment > max_allowed_segment:
        raise RuntimeError(
            "Cannot use a Transformer model with a longer segment than it was trained for. "
            f"Maximum segment is: {max_allowed_segment}"
        )

    model.cpu()
    model.eval()

    wav = load_track(Path(args.input), model.audio_channels, model.samplerate)
    ref = wav.mean(0)
    ref_mean = ref.mean()
    ref_std = ref.std()
    if float(ref_std) < 1e-8:
        ref_std = ref_std.new_tensor(1.0)

    wav = wav - ref_mean
    wav = wav / ref_std
    sources = apply_model(
        model,
        wav[None],
        device=args.device,
        shifts=args.shifts,
        split=True,
        overlap=args.overlap,
        progress=False,
        num_workers=args.jobs,
        segment=args.segment,
    )[0]
    sources = sources * ref_std
    sources = sources + ref_mean

    output_dir = Path(args.output_root) / args.model / Path(args.input).stem
    output_dir.mkdir(parents=True, exist_ok=True)
    for source, name in zip(sources, model.sources):
        _write_stem(source, output_dir / f"{name}.wav", model.samplerate)


if __name__ == "__main__":
    main()