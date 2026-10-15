"""Small GitHub client for the one-off data pull. The app itself never calls GitHub:
everything fetched here is cached as JSON under data/raw/ and loaded from there."""

import os
import subprocess
import sys
import time

import httpx

API = "https://api.github.com"


def token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Set GITHUB_TOKEN or sign in with `gh auth login` first.")
    return out.stdout.strip()


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class GitHub:
    def __init__(self) -> None:
        self.http = httpx.Client(
            headers={
                "Authorization": f"Bearer {token()}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "understudy-fetch",
            },
            timeout=60,
        )

    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(8):
            try:
                r = self.http.request(method, url, **kw)
            except httpx.TransportError as e:
                wait = 2**attempt
                log(f"network error ({e.__class__.__name__}), retrying in {wait}s")
                time.sleep(wait)
                continue
            if r.status_code in (403, 429) and (
                r.headers.get("retry-after") or r.headers.get("x-ratelimit-remaining") == "0"
            ):
                if r.headers.get("retry-after"):
                    wait = int(r.headers["retry-after"]) + 1
                else:
                    wait = (
                        max(int(r.headers.get("x-ratelimit-reset", "0")) - int(time.time()), 1) + 5
                    )
                log(f"rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                wait = 2**attempt
                log(f"HTTP {r.status_code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"giving up on {method} {url}")

    def rest(self, path: str, **params) -> dict | list:
        return self._send("GET", API + path, params=params).json()

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Returns `data`. GitHub sometimes answers 200 with a timeout or rate-limit
        error in the body, so those are retried here too."""
        for attempt in range(6):
            body = self._send(
                "POST", API + "/graphql", json={"query": query, "variables": variables or {}}
            ).json()
            errors = body.get("errors") or []
            types = {e.get("type") for e in errors}
            if "RATE_LIMITED" in types:
                log("GraphQL rate limit hit, sleeping 10 minutes")
                time.sleep(600)
                continue
            if body.get("data") is None:
                wait = 2**attempt * 5
                log(f"GraphQL error {errors[:1]}, retrying in {wait}s")
                time.sleep(wait)
                continue
            # Partial errors (a missing blob, say) come with data; callers handle the nulls.
            return body["data"]
        raise RuntimeError(f"GraphQL kept failing: {errors[:2]}")
