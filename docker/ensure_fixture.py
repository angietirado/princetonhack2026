"""One-shot Compose initializer; preserve any existing fixture."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from spending_generator.cli import build_fixture


def main():
    path = Path(".data/demo.json")
    if path.exists():
        print("Keeping existing demo fixture.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture(seed=42, days=90, end=datetime.now(UTC).date())
    # Publish a complete file without replacing one created by another initializer.
    # Both paths are on the named volume, so the hard link is atomic.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".demo-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(json.dumps(fixture, indent=2) + "\n")
        os.link(temporary, path)
    except FileExistsError:
        print("Keeping existing demo fixture.")
        return
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("Created demo fixture in the app-data volume.")


if __name__ == "__main__":
    main()
