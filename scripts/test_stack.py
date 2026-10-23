"""Isolated project and tmpfs PostgreSQL. Never touches development volumes."""

import os
import shlex
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

kind = sys.argv[1]
if kind not in ("integration", "e2e"):
    raise SystemExit("Expected integration/e2e")
compose_file = str(Path("docker-compose.test.yml").resolve())
if os.environ.get("COMPOSE_HOST_PATH_PREFIX"):
    compose_file = compose_file.removeprefix(os.environ["COMPOSE_HOST_PATH_PREFIX"])
cmd = shlex.split(os.environ.get("COMPOSE", "docker compose")) + [
    "-f",
    compose_file,
    "-p",
    "alfa-test-" + uuid4().hex[:10],
]


def run(*args):
    subprocess.run(cmd + list(args), check=True)


try:
    run("build", "app")
    run("up", "-d", "app", *(["worker"] if kind == "e2e" else []))
    run("run", "--rm", "tests", "pytest", "tests/" + kind, "-q")
finally:
    run("down")
