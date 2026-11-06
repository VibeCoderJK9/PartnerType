"""Server messages come back in the language the app asked for.

The web app sends its language switch as `Accept-Language`; with no header,
or none the app speaks, the server answers in English.
"""


def _login(call_api, user, headers=None):
    return call_api(
        "POST", "/api/auth/login",
        body={"email": user["email"], "password": user["plain_password"], "role": user["role"]},
        headers=headers,
    )


def test_unauthorized_answer_follows_the_request_language(call_api):
    _, polish = call_api("GET", "/api/leads", headers={"Accept-Language": "pl"})
    _, english = call_api("GET", "/api/leads", headers={"Accept-Language": "en"})

    assert polish["error"] == "Nieautoryzowany dostęp. Zaloguj się."
    assert english["error"] == "Unauthorized. Please sign in."


def test_a_domain_validation_message_follows_the_request_language(call_api, partner_user):
    _, session = _login(call_api, partner_user)
    token = session["token"]

    _, polish = call_api("POST", "/api/leads/precheck", body={"nip": "123"}, token=token)
    _, english = call_api("POST", "/api/leads/precheck", body={"nip": "123"}, token=token,
                          headers={"Accept-Language": "en"})

    assert polish["error"] == "Pole 'NIP' musi składać się z 10 cyfr."
    assert english["error"] == "Field 'NIP' must consist of 10 digits."


def test_a_wrong_password_is_refused_in_english(call_api, partner_user):
    status, body = call_api(
        "POST", "/api/auth/login",
        body={"email": partner_user["email"], "password": "not-the-password", "role": "partner"},
        headers={"Accept-Language": "en-GB,en;q=0.9"},
    )

    assert status == 401
    assert body["error"] == "Invalid e-mail, password or role."


def test_an_unsupported_language_gets_english(call_api):
    _, body = call_api("GET", "/api/leads", headers={"Accept-Language": "de-DE"})

    assert body["error"] == "Unauthorized. Please sign in."


def test_no_language_at_all_gets_english(call_api):
    _, body = call_api("GET", "/api/leads", headers={"Accept-Language": None})

    assert body["error"] == "Unauthorized. Please sign in."


def test_a_german_browser_that_also_reads_polish_gets_polish(call_api):
    _, body = call_api("GET", "/api/leads", headers={"Accept-Language": "de-DE,de;q=0.9,pl;q=0.7"})

    assert body["error"] == "Nieautoryzowany dostęp. Zaloguj się."


def test_the_language_does_not_leak_into_the_next_request(call_api):
    call_api("GET", "/api/leads", headers={"Accept-Language": "en"})
    _, body = call_api("GET", "/api/leads", headers={"Accept-Language": "pl"})

    assert body["error"] == "Nieautoryzowany dostęp. Zaloguj się."
