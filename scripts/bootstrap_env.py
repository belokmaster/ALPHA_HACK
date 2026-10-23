"""Create .env exactly once, without reading or printing existing secrets."""

import os
import secrets
from pathlib import Path

try:
    fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing .env preserved")
else:
    template = (
        Path(".env.example")
        .read_text()
        .replace("APP_SECRET=\n", "APP_SECRET=" + secrets.token_urlsafe(48) + "\n")
    )
    with os.fdopen(fd, "w") as f:
        f.write(template)
    print("Local .env created; secret not printed")
