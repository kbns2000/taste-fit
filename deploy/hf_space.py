"""Deploy Taste-Fit to a Hugging Face Space (Docker SDK, free CPU tier).

Usage (secrets come from the environment; nothing is printed or written):
    HF_TOKEN=... QLOO_API_KEY=... python deploy/hf_space.py --space <user>/taste-fit

What it does
  1. creates the Space if needed (sdk=docker, public so judges can open it);
  2. sets Space *secrets* for every key present in the environment
     (QLOO_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY) and plain *variables*
     for the non-secret settings;
  3. uploads the app folder (tests, deploy scripts and the virtualenv excluded);
  4. prints the Space page and the app URL.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SECRET_KEYS = ["QLOO_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
VARIABLE_KEYS = ["QLOO_BASE_URL", "QLOO_MODE", "QLOO_MAX_CALLS_PER_RUN", "QLOO_DAILY_BUDGET",
                 "ANTHROPIC_MODEL", "ANTHROPIC_EFFORT", "OPENAI_BASE_URL", "OPENAI_MODEL", "LLM_MODE",
                 "LLM_MAX_TOOL_CALLS", "RUNS_PER_IP_PER_10MIN", "PUBLIC_REPO_URL"]
IGNORE = [".venv/*", ".git/*", ".github/*", "tests/*", "deploy/*", "scripts/*", "docs/*", "__pycache__/*",
          "*/__pycache__/*", ".pytest_cache/*", ".env", ".env.*", "*.log", ".dockerignore"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space", required=True, help="<hf-username>/<space-name>")
    parser.add_argument("--private", action="store_true", help="create the Space as private")
    parser.add_argument("--no-upload", action="store_true", help="only create the Space and set secrets")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN is not set (a Hugging Face token with write access is required).", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi  # dev dependency

    api = HfApi(token=token)
    repo_id = args.space
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=args.private, exist_ok=True)
    print(f"space ready: https://huggingface.co/spaces/{repo_id}")

    for key in SECRET_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            api.add_space_secret(repo_id, key, value)
            print(f"secret set: {key} (value not shown)")
    for key in VARIABLE_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            api.add_space_variable(repo_id, key, value)
            print(f"variable set: {key}={value}")

    if not args.no_upload:
        root = Path(__file__).resolve().parent.parent
        stray = [p.name for p in root.glob(".env*") if p.name != ".env.example"]
        if stray:
            print(f"refusing to upload: remove local env files first: {', '.join(stray)}", file=sys.stderr)
            return 3
        api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(root),
                          ignore_patterns=IGNORE, commit_message="deploy Taste-Fit")
        print("uploaded; the Space will build the Docker image (2-4 minutes).")
    owner, name = repo_id.split("/", 1)
    print(f"app url: https://{owner}-{name.replace('_', '-')}.hf.space")
    return 0


if __name__ == "__main__":
    sys.exit(main())
