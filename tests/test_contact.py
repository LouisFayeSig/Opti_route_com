from urllib.parse import parse_qs, urlsplit

import pytest

from opti_route.contact import account_request_mailto


@pytest.mark.parametrize("action,preposition", [("ajouter", "au"), ("modifier", "au"), ("supprimer", "du")])
def test_helpdesk_draft_keeps_glpi_subject_and_body(action, preposition):
    url = urlsplit(account_request_mailto(" Faye   Louis ", action, "ATC"))
    assert url.scheme == "mailto"
    assert url.path == "SIG-ROBOTIT@sigplc.fr"
    assert parse_qs(url.query) == {
        "subject": ["Groupe AD Faye Louis OptiRoute"],
        "body": [f"J'aimerais {action} Faye Louis {preposition} groupe ATC"],
    }


def test_recipient_can_be_configured_and_cannot_inject_mail_headers():
    assert account_request_mailto("Jean Dupont", "ajouter", "RAG", recipient="agent@example.fr").startswith("mailto:agent@example.fr?")
    with pytest.raises(ValueError):
        account_request_mailto("Jean Dupont", "ajouter", "RAG", recipient="agent@example.fr?bcc=autre@example.fr")
