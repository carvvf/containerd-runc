"""GitHub and HTTPS access shared by input resolution, publication and retention."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

API = "https://api.github.com/"
USER_AGENT = "containerd-runc"


@lru_cache(maxsize=1)
def cli_github_token():
    """Read existing CLI credentials once, without exposing them in logs or files."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def github_authentication():
    for name in ["GH_TOKEN", "GITHUB_TOKEN"]:
        if token := os.environ.get(name):
            return token, name
    token = cli_github_token()
    return token, "GitHub CLI" if token else "anonymous"


def rate_limit_error(error, source):
    body = error.read().decode("utf-8", errors="replace")
    if not (error.headers.get("X-RateLimit-Remaining") == "0"
            or error.headers.get("Retry-After")
            or "rate limit" in body.lower()):
        return None
    reset = error.headers.get("X-RateLimit-Reset", "")
    reset_time = datetime.fromtimestamp(int(reset), timezone.utc).isoformat() if reset.isdigit() else "not provided"
    retry = error.headers.get("Retry-After", "not provided")
    guidance = ("Set GH_TOKEN/GITHUB_TOKEN or authenticate with gh auth login --hostname github.com."
                if source == "anonymous" else "Wait for the indicated reset before retrying.")
    return ValueError(
        f"GitHub API rate limit exceeded (authentication: {source}; "
        f"reset UTC: {reset_time}; retry-after seconds: {retry}). {guidance}"
    )


def api_request(url, method="GET", data=None, content_type="application/json", accept="application/vnd.github+json"):
    """Send one authenticated API request; the token is only ever sent to GitHub's API hosts."""
    if not (url.startswith(API) or url.startswith("https://uploads.github.com/")):
        raise ValueError(f"Unexpected GitHub API URL: {url}")
    headers = {"Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    token, source = github_authentication()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = response.read()
            return (json.loads(raw) if raw else None), response.headers.get("Link", "")
    except urllib.error.HTTPError as error:
        if error.code in {403, 429} and (limit := rate_limit_error(error, source)):
            raise limit from error
        raise


def request_json(url):
    return api_request(url)


def github_pages(url, request=request_json):
    """Read every page; errors must propagate rather than returning partial results."""
    while url:
        if not url.startswith(API):
            raise ValueError("Unexpected GitHub pagination URL")
        page, links = request(url)
        if not isinstance(page, list):
            raise ValueError("Expected a JSON list page from the GitHub API")
        yield from page
        match = re.search(r'<([^>]+)>; rel="next"', links)
        url = match[1] if match else ""


def download(url, timeout=300):
    """Fetch public content over HTTPS without credentials."""
    if not url.startswith("https://"):
        raise ValueError("HTTPS is required for build inputs")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()
