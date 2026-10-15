"""The seven-person sample org shown on the landing page. Billing has three people
who know it, but every route to Payments runs through Dev, so its understudy number
is 1."""

SAMPLE_ORG = {
    "teams": ["payments", "growth", "platform"],
    "systems": [
        {"name": "billing", "teams": ["payments"]},
        {"name": "ledger", "teams": ["payments"]},
        {"name": "search", "teams": ["growth"]},
        {"name": "auth", "teams": ["platform", "growth"]},
        {"name": "deploys", "teams": ["platform"]},
    ],
    "people": [
        {"handle": "ana", "teams": ["growth"]},
        {"handle": "ben", "teams": ["growth"]},
        {"handle": "cara", "teams": ["platform"]},
        {"handle": "dev", "teams": ["payments"]},
        {"handle": "esi", "teams": ["growth"]},
        {"handle": "femi", "teams": ["platform"]},
        {"handle": "gus", "teams": ["platform"]},
    ],
    "knows": [
        {"person": p, "system": s, "score": 1.0}
        for s, ps in {
            "billing": ["ana", "ben", "cara"],
            "ledger": ["cara"],
            "search": ["esi", "femi"],
            "auth": ["dev", "gus", "ana"],
            "deploys": ["gus", "femi"],
        }.items()
        for p in ps
    ],
    "collaborates": [
        ["ana", "dev"],
        ["ben", "dev"],
        ["cara", "dev"],
        ["esi", "femi"],
        ["gus", "femi"],
        ["ana", "esi"],
        ["femi", "ben"],
    ],
}

SAMPLE_NUMBERS = {"billing": 1, "ledger": 1, "search": 2, "auth": 3, "deploys": 2}
