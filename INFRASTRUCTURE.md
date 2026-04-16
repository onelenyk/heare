# Heare Infrastructure Guide

This document describes the infrastructure components for running Heare as a system service.

## Components

### 1. Control Script (`hearectl`)

The main control script for managing Heare:

```bash
./hearectl start      # Start daemon
./hearectl stop       # Stop daemon
./hearectl restart    # Restart daemon
./hearectl status     # Check status
./hearectl logs       # View logs
./hearectl watch      # Start watch dashboard
./hearectl install    # Install as systemd service
./hearectl uninstall  # Remove systemd service
```

### 2. Systemd Service (`heare.service`)

Installs Heare as a user systemd service with:
- Auto-start on login
- Auto-restart on failure
- Resource limits (500MB memory, 200% CPU)
- Security hardening (no new privileges, private /tmp)
- Journal logging

### 3. Makefile

Convenience commands for development:

```bash
make quickstart     # Initial setup
make dev            # Start development server
make test           # Run tests
make start          # Start daemon (via hearectl)
make stop           # Stop daemon (via hearectl)
make logs           # Tail logs (via hearectl)
make install        # Install systemd service
make lint           # Run linter
make format         # Format code
```

### 4. Quick Start Script (`quickstart.sh`)

Automated setup that:
- Checks dependencies (uv, python3)
- Creates .env from .env.example
- Installs Python packages
- Creates necessary directories
- Installs portaudio for audio support

### 5. Log Rotation (`heare.logrotate`)

Configures log rotation for:
- Daily rotation
- 7 days retention
- Compression of old logs

## Installation

### First Time Setup

1. **Run quickstart:**
   ```bash
   ./quickstart.sh
   ```

2. **Edit .env file:**
   ```bash
   nano .env
   # Add your GROQ_API_KEY
   ```

3. **Install as service (optional):**
   ```bash
   ./hearectl install
   ```

## Usage

### Development Mode

Start directly in terminal:
```bash
make dev
# or
./quickstart.sh
```

### Production Mode (Systemd)

Install as service, then control with systemd:
```bash
./hearectl install
systemctl --user start heare
systemctl --user stop heare
systemctl --user status heare
journalctl --user -u heare -f  # View logs
```

### Manual Control

Use the control script:
```bash
./hearectl start
./hearectl status
./hearectl logs
./hearectl stop
```

## File Locations

| File | Location | Purpose |
|------|----------|---------|
| **Daemon** | `./heare.service` | Systemd service definition |
| **Control** | `./hearectl` | Main control script |
| **Makefile** | `./Makefile` | Development commands |
| **Environment** | `~/.heare/heare.env` | Environment for systemd |
| **Logs** | `~/.heare/logs/daemon.log` | Daemon logs |
| **PID file** | `~/.heare/heare.pid` | Process ID |
| **Database** | `~/.heare/heare.db` | Transcript storage |
| **Config** | `~/.heare/config.toml` | Settings |

## Troubleshooting

### Daemon won't start

```bash
# Check status
./hearectl status

# Check logs
./hearectl logs

# Verify API key
grep GROQ_API_KEY .env

# Check dependencies
uv sync
```

### Audio issues

```bash
# Verify portaudio
brew list portaudio

# Reinstall if needed
brew reinstall portaudio
uv sync --extra local
```

### Service issues

```bash
# Check service status
systemctl --user status heare

# View logs
journalctl --user -u heare -n 50

# Restart service
systemctl --user restart heare
```

## Security Notes

- **Local-only**: Heare runs as a user service, not system-wide
- **No network exposure**: No open ports or network services
- **Data storage**: All data stored in user home directory (`~/.heare`)
- **API keys**: Loaded from environment file, never logged
- **Passphrase redaction**: Confirmation passphrase never logged

## Resource Usage

Typical daemon usage:
- **Memory**: 150-200MB
- **CPU**: <5% idle, 20-30% during speech
- **Disk**: ~10MB/day in logs + database growth
- **Network**: 1-2 Groq STT calls per utterance

## Monitoring

### Check health

```bash
./hearectl status
# Shows: running status, PID, memory, uptime, recent logs
```

### View logs

```bash
# Live logs
./hearectl logs

# Or with systemd
journalctl --user -u heare -f
```

### Performance metrics

Heare includes internal performance tracking:
- Decider call timing
- STT latency
- Speaker embedding duration
- Event queue depth

Check logs for `[TIMING]` prefixes.

## Development

### Running tests

```bash
make test
# or
uv run pytest -v
```

### Code quality

```bash
make lint          # Check code quality
make lint-fix      # Auto-fix issues
make format        # Format code
make check         # Run all checks
```

### Database inspection

```bash
sqlite3 ~/.heare/heare.db

# View recent transcripts
SELECT ts, text FROM transcripts ORDER BY ts DESC LIMIT 10;

# View decisions
SELECT * FROM decisions ORDER BY ts DESC LIMIT 10;

# View actions
SELECT * FROM actions ORDER BY ts DESC LIMIT 10;
```

## Upgrading

### Update code

```bash
git pull
uv sync
./hearectl restart
```

### Update dependencies

```bash
uv sync --upgrade
./hearectl restart
```

## Uninstallation

### Remove service

```bash
./hearectl uninstall
```

### Clean up data (optional)

```bash
rm -rf ~/.heare
```

Note: This removes all data, transcripts, and settings!
