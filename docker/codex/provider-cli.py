#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
kind, variable, host_variable, default_host = {
    "gh": ("github", "GH_TOKEN", "GH_HOST", "github.com"),
    "glab": ("gitlab", "GITLAB_TOKEN", "GITLAB_HOST", "gitlab.com"),
}[tool]
host = os.environ.get(host_variable, default_host)
for index, argument in enumerate(sys.argv[1:], 1):
    if argument == "--hostname" and index + 1 < len(sys.argv):
        host = sys.argv[index + 1]
    elif argument.startswith("--hostname="):
        host = argument.partition("=")[2]
path = Path.home() / ".ssh/cli-tokens.json"
records = json.loads(path.read_text()) if path.exists() else []
record = next((record for record in records if record["kind"] == kind and record["host"] == host), None)
environment = dict(os.environ)
for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN"):
    environment.pop(key, None)
if record is not None:
    variable = "GH_ENTERPRISE_TOKEN" if kind == "github" and host != "github.com" else variable
    environment[variable] = record["token"]
    environment[host_variable] = host
os.execve(f"/usr/bin/{tool}", [tool, *sys.argv[1:]], environment)
