import os
from dataclasses import dataclass
from pathlib import Path

# Load .env if present
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

@dataclass
class Cfg:
    BOT_TOKEN:      str   = os.getenv("FLEET_TOKEN", "")
    MASTER_KEY:     str   = os.getenv("FLEET_KEY", "")       # 64 hex chars (32 bytes)
    VENICE_KEY:     str   = os.getenv("VENICE_API_KEY", "")
    DB_PATH:        str   = os.getenv("FLEET_DB", "fleet.enc")
    HEARTBEAT_SEC:  int   = int(os.getenv("HEARTBEAT_SEC", "300"))   # 5 min
    SLEEP_SEC:      int   = int(os.getenv("SLEEP_SEC", "3600"))       # 1 hour
    MAX_STM:        int   = int(os.getenv("MAX_STM", "200"))
    DECAY_RATE:     float = float(os.getenv("DECAY_RATE", "0.02"))

C = Cfg()
