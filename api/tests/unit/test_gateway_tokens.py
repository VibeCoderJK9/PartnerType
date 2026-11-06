"""Gateway secret helpers: shapes, fingerprints and the RFC 7636 PKCE vector."""
import re

from api.gateway import tokens

# RFC 7636 Appendix B.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_rfc7636_appendix_b_vector_matches():
    assert tokens.pkce_s256_matches(RFC_VERIFIER, RFC_CHALLENGE) is True


def test_pkce_rejects_short_verifier():
    assert tokens.pkce_s256_matches(RFC_VERIFIER[:42], RFC_CHALLENGE) is False


def test_pkce_rejects_verifier_with_plus():
    assert tokens.pkce_s256_matches(RFC_VERIFIER[:-1] + "+", RFC_CHALLENGE) is False


def test_pkce_rejects_wrong_challenge():
    wrong = RFC_CHALLENGE[:-1] + ("A" if RFC_CHALLENGE[-1] != "A" else "B")
    assert tokens.pkce_s256_matches(RFC_VERIFIER, wrong) is False


def test_pkce_rejects_non_strings_and_bad_challenge_shape():
    assert tokens.pkce_s256_matches(None, RFC_CHALLENGE) is False
    assert tokens.pkce_s256_matches(RFC_VERIFIER, None) is False
    assert tokens.pkce_s256_matches(RFC_VERIFIER, RFC_CHALLENGE + "=") is False


def test_new_token_has_prefix_and_256_bits_and_is_unique():
    first = tokens.new_token(tokens.KEY_PREFIX)
    second = tokens.new_token(tokens.KEY_PREFIX)
    assert first.startswith("ptk_")
    assert len(first) - len("ptk_") >= 43
    assert first != second


def test_new_token_refuses_unknown_prefix():
    try:
        tokens.new_token("xyz_")
    except ValueError:
        return
    raise AssertionError("unknown prefix accepted")


def test_token_hash_is_64_lowercase_hex():
    digest = tokens.token_hash(tokens.new_token(tokens.ACCESS_PREFIX))
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_display_prefix_is_type_prefix_plus_eight_characters():
    raw = tokens.new_token(tokens.KEY_PREFIX)
    assert tokens.display_prefix(raw) == raw[:12]


def test_client_id_is_prefixed():
    assert tokens.new_client_id().startswith("ptc_")
