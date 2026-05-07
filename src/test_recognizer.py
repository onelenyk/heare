#!/usr/bin/env python3
"""Interactive speaker recognition tester.

Records short audio clips and shows recognition results in real-time.
Useful for tuning thresholds and debugging enrollment issues.
"""

from __future__ import annotations

import asyncio
import sys

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config import Settings
from src.voice.speaker.gallery import SpeakerGallery
from src.voice.speaker import id as speaker_id_mod

console = Console()

# Lazy-load the model
_model = None


def get_model():
    global _model
    if _model is None:
        console.print("[dim]Loading ECAPA model...[/dim]")
        _model = speaker_id_mod.load_model()
    return _model


def format_score(score: float, threshold: float) -> str:
    if score >= threshold:
        return f"[green bold]{score:.2f} ✓[/green bold]"
    return f"[red]{score:.2f} ✗[/red]"


async def record_audio(duration_ms: int = 3000, sample_rate: int = 16000) -> tuple[np.ndarray, int]:
    """Record audio from microphone."""
    try:
        import pyaudio

        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=1024,
        )

        frames = []
        num_frames = int(sample_rate * duration_ms / 1000 / 1024) + 1

        console.print(f"[dim]Recording {duration_ms}ms...[/dim]", end="")
        for _ in range(num_frames):
            data = stream.read(1024, exception_on_overflow=False)
            frames.append(data)

        console.print(" [green]Done[/green]")

        stream.stop_stream()
        stream.close()
        p.terminate()

        audio = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        return audio, sample_rate
    except ImportError:
        console.print("[red]pyaudio not installed. Run: pip install pyaudio[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Recording failed: {e}[/red]")
        sys.exit(1)


async def test_loop(settings: Settings, gallery: SpeakerGallery):
    """Main interactive test loop."""
    threshold = settings.speaker_id_threshold_match
    duration = 3000  # ms

    console.print(Panel.fit(
        f"[bold]Speaker Recognition Tester[/bold]\n"
        f"Threshold: {threshold} | Duration: {duration}ms\n"
        f"Enrolled: {', '.join(gallery.list_speakers())}\n\n"
        f"[dim]Commands:[/dim]\n"
        f"  [cyan]<Enter>[/cyan] - Record and test\n"
        f"  [cyan]+/-[/cyan] - Adjust threshold (±0.05)\n"
        f"  [cyan]d +/-[/cyan] - Adjust duration (±500ms)\n"
        f"  [cyan]r[/cyan] - Re-enroll owner\n"
        f"  [cyan]s[/cyan] - Show gallery stats\n"
        f"  [cyan]q[/cyan] - Quit"
    ))

    while True:
        try:
            cmd = await asyncio.get_event_loop().run_in_executor(
                None, input, "\n> "
            )
            cmd = cmd.strip().lower()

            if not cmd:
                audio, sr = await record_audio(duration)
                if len(audio) < sr * duration / 1000 / 2:
                    console.print("[yellow]Audio too short, try again[/yellow]")
                    continue

                console.print("[dim]Computing embedding...[/dim]")
                emb = speaker_id_mod.embed(audio, sr, get_model())

                table = Table(title="Recognition Results")
                table.add_column("Speaker", style="cyan")
                table.add_column("Score", justify="right")
                table.add_column("Match", justify="center")

                for sid in gallery.list_speakers():
                    label = gallery.get_label(sid)
                    # Test against this speaker's embeddings
                    entry = gallery._speakers.get(sid)
                    if not entry or not entry.get("embeddings"):
                        continue

                    refs = np.asarray(entry["embeddings"], dtype=np.float32)
                    ref_norms = np.linalg.norm(refs, axis=1) + 1e-12
                    v_norm = float(np.linalg.norm(emb)) + 1e-12
                    scores = (refs @ emb) / (ref_norms * v_norm)
                    score = float(scores.max())

                    table.add_row(
                        f"{sid} ({label})",
                        f"{score:.3f}",
                        "✓" if score >= threshold else "✗",
                    )

                    if score >= threshold and sid == "owner":
                        console.print(Panel.fit(
                            f"[green bold]MATCHED: {label}[/green bold]\n"
                            f"Score: {score:.3f} / {threshold}",
                            title="✓ Recognition"
                        ))

                console.print(table)

            elif cmd == "+":
                threshold = min(1.0, threshold + 0.05)
                console.print(f"[cyan]Threshold: {threshold:.2f}[/cyan]")

            elif cmd == "-":
                threshold = max(0.0, threshold - 0.05)
                console.print(f"[cyan]Threshold: {threshold:.2f}[/cyan]")

            elif cmd.startswith("d "):
                parts = cmd.split()
                if len(parts) > 1:
                    try:
                        delta = int(parts[1])
                        duration = max(500, min(10000, duration + delta))
                        console.print(f"[cyan]Duration: {duration}ms[/cyan]")
                    except ValueError:
                        console.print("[red]Invalid duration value. Use: d +/-500[/red]")

            elif cmd == "r":
                console.print("\n[bold]Re-enrolling owner...[/bold]")
                audio, sr = await record_audio(15000)
                emb = speaker_id_mod.embed(audio, sr, get_model())
                existing_label = gallery.get_label("owner") or "owner"
                gallery.enroll_owner(emb, existing_label)
                console.print("[green]Owner enrolled[/green]")

            elif cmd == "s":
                console.print("\n[bold]Gallery Stats:[/bold]")
                for sid in gallery.list_speakers():
                    entry = gallery._speakers.get(sid)
                    if entry:
                        console.print(
                            f"  {sid}: {entry.get('label')} - "
                            f"{len(entry.get('embeddings', []))} embeddings"
                        )

            elif cmd in ("q", "quit", "exit"):
                console.print("[yellow]Goodbye![/yellow]")
                break

        except KeyboardInterrupt:
            console.print("\n[yellow]Goodbye![/yellow]")
            break
        except EOFError:
            break


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test speaker recognition interactively")
    parser.add_argument("--threshold", type=float, default=None, help="Override threshold")
    parser.add_argument("--duration", type=int, default=None, help="Recording duration (ms)")
    args = parser.parse_args()

    settings = Settings()
    if args.threshold is not None:
        settings.speaker_id_threshold_match = args.threshold

    gallery = SpeakerGallery.load(settings.speakers_file)
    console.print(f"[dim]Loaded gallery: {gallery.list_speakers()}[/dim]")

    await test_loop(settings, gallery)


if __name__ == "__main__":
    asyncio.run(main())
