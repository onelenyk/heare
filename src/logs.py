#!/usr/bin/env python3
"""Watch heare logs in real-time."""
import argparse
from pathlib import Path

def watch_logs(log_file: Path, follow: bool = True):
    """Tail the log file."""
    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        print("Start heare first: uv run python -m src.main start")
        return

    print(f"Watching: {log_file}")
    print("=" * 60)

    try:
        with open(log_file, "r") as f:
            # Show last 20 lines first
            lines = f.readlines()
            for line in lines[-20:]:
                print(line.rstrip())

            if follow:
                print("=" * 60)
                print("Waiting for new logs... (Ctrl+C to exit)")
                print("=" * 60)

                # Follow mode
                f.seek(0, 2)  # Seek to end
                while True:
                    line = f.readline()
                    if line:
                        print(line.rstrip())
                    else:
                        import time
                        time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopped.")

def main():
    parser = argparse.ArgumentParser(description="Watch heare logs")
    parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log file (like tail -f)",
    )
    parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=20,
        help="Number of recent lines to show (default: 20)",
    )
    args = parser.parse_args()

    log_file = Path.home() / ".heare" / "logs" / "daemon.log"
    watch_logs(log_file, follow=args.follow)

if __name__ == "__main__":
    main()
