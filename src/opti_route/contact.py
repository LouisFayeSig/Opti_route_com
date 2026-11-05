"""Liens de contact ouvrant un brouillon dans la messagerie de l'utilisateur."""

from __future__ import annotations

from urllib.parse import quote, urlencode

CONTACT_EMAIL = "l.faye@sigplc.fr"


def contact_mailto(subject: str, body: str) -> str:
    body = body.replace("\r\n", "\n").replace("\n", "\r\n")
    query = urlencode({"subject": subject, "body": body}, quote_via=quote)
    return f"mailto:{CONTACT_EMAIL}?{query}"


def account_request_mailto() -> str:
    return contact_mailto(
        "Optiroute - Ajout / modification / suppression de compte",
        "Bonjour,\n\n"
        "Je souhaite demander (conserver la mention utile) :\n"
        "Ajout / Modification / Suppression de compte\n\n"
        "Personne concernée par la demande :\n"
        "Nom et prénom : \n"
        "Adresse e-mail professionnelle : \n"
        "Poste / fonction : \n"
        "Agence / région : \n\n"
        "Détails de la demande : \n\n"
        "Nom du demandeur : \n\n"
        "Merci.",
    )


def feedback_mailto(display_name: str = "", email: str = "") -> str:
    return contact_mailto(
        "Optiroute - Suggestion ou avis",
        "Bonjour,\n\n"
        "Je souhaite partager une suggestion ou un avis sur Optiroute.\n\n"
        f"Nom et prénom : {display_name}\n"
        f"Adresse e-mail : {email}\n"
        "Poste / fonction : \n\n"
        "Fonctionnalité concernée : \n"
        "Ma suggestion / mon avis : \n\n"
        "Merci.",
    )


def support_mailto(display_name: str = "", email: str = "") -> str:
    return contact_mailto(
        "Optiroute - Demande de support",
        "Bonjour,\n\n"
        "J'ai besoin d'aide avec Optiroute.\n\n"
        f"Nom et prénom : {display_name}\n"
        f"Adresse e-mail : {email}\n"
        "Poste / fonction : \n\n"
        "Écran / fonctionnalité concerné(e) : \n"
        "Problème rencontré / message d'erreur : \n"
        "Étapes pour reproduire le problème : \n"
        "Date et heure du problème : \n\n"
        "Merci.",
    )
