"""Who counts as a person, and what they are called in the app.

Handles are public, but a page saying "Kubernetes breaks if @someone leaves" is not
okay, so everyone gets a stable pseudonym and real handles never reach the graph."""

import hashlib
import hmac
import re

# Prow's robots are ordinary user accounts on GitHub, so __typename alone misses them.
BOT_NAMES = {
    "k8s-ci-robot",
    "k8s-triage-robot",
    "k8s-infra-ci-robot",
    "k8s-release-robot",
    "k8s-cherrypick-robot",
    "kubernetes-prow",
    "linux-foundation-easycla",
    "dependabot",
    "codecov",
    "netlify",
    "fejta-bot",
}
BOT_RE = re.compile(r"(\[bot\]|-bot|-robot)$", re.IGNORECASE)


def is_bot(login: str | None, typed_bot: bool = False) -> bool:
    # A missing login is a deleted account; nobody can step in for or as a ghost.
    return not login or typed_bot or login.lower() in BOT_NAMES or bool(BOT_RE.search(login))


def pseudonyms(logins: set[str], salt: str) -> dict[str, str]:
    """contributor-xxxx from a salted hash. Four hex digits collide often with a few
    thousand people, so the length grows until every name is unique. Pass every login
    in the raw data so the names stay the same across as-of snapshots."""
    digests = {
        login: hmac.new(salt.encode(), login.lower().encode(), hashlib.sha256).hexdigest()
        for login in logins
    }
    for length in range(4, 17):
        names = {login: f"contributor-{d[:length]}" for login, d in digests.items()}
        if len(set(names.values())) == len(names):
            return names
    raise RuntimeError("pseudonyms still collide at 16 hex digits")
