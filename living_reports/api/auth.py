"""Seeded demo users. A real identity system replaces this after the MVP."""
ROLES = {"ana": "author", "omar": "owner", "priya": "owner", "chidi": "compliance"}


def role_of(user: str) -> str | None:
    return ROLES.get(user)
