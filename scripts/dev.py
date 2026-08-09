from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_api.server import create_server  # noqa: E402


def main() -> None:
    host = os.environ.get("COPILOT_HOST", "127.0.0.1")
    port = int(os.environ.get("COPILOT_PORT", "8765"))
    server = create_server(ROOT, host, port)
    print(f"Pokémon Champions Battle Copilot: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
