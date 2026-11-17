"""Liens de contact ouvrant un brouillon dans la messagerie de l'utilisateur."""

from __future__ import annotations

from urllib.parse import quote, urlencode

CONTACT_EMAIL = "l.faye@sigplc.fr"
ACCOUNT_ACTIONS = ("ajouter", "modifier", "supprimer")
ACCOUNT_GROUPS = ("DR", "ATC", "ADMIN", "RAG")


def contact_mailto(subject: str, body: str) -> str:
    body = body.replace("\r\n", "\n").replace("\n", "\r\n")
    query = urlencode({"subject": subject, "body": body}, quote_via=quote)
    return f"mailto:{CONTACT_EMAIL}?{query}"


def account_request_mailto(display_name: str, action: str, group: str) -> str:
    display_name = " ".join(display_name.split())
    if not display_name:
        raise ValueError("Le nom et le prénom sont obligatoires.")
    if action not in ACCOUNT_ACTIONS or group not in ACCOUNT_GROUPS:
        raise ValueError("Le type de demande ou le groupe AD est invalide.")
    preposition = "du" if action == "supprimer" else "au"
    return contact_mailto(
        f"Groupe AD {display_name} OptiRoute",
        f"J'aimerais {action} {display_name} {preposition} groupe {group}",
    )
