"""Pure helpers of the knowledge base: names, types, limits, headers."""
import pytest

from api import knowledge


@pytest.mark.parametrize("raw,expected", [
    ("price-list.pdf", "price-list.pdf"),
    ("C:\\Users\\someone\\price-list.pdf", "price-list.pdf"),
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("..hidden.pdf", "hidden.pdf"),
    ('a"b\r\n.pdf', "ab.pdf"),
    (". .pdf", "pdf"),
    # Direction overrides and invisible marks could make "fdp.exe" read as
    # "exe.pdf" on the card or in the save dialog.
    ("invoice\u202efdp.exe.pdf", "invoicefdp.exe.pdf"),
    ("a\u2066b\u2069\u200e\u200f\u061c.pdf", "ab.pdf"),
    ("\ufeff\u200bprice.pdf", "price.pdf"),
    (" . hidden.pdf", "hidden.pdf"),
    ("", ""),
    (None, ""),
])
def test_clean_filename(raw, expected):
    assert knowledge.clean_filename(raw) == expected


def test_long_filename_keeps_its_extension():
    name = knowledge.clean_filename("x" * 300 + ".pdf")
    assert len(name) == knowledge.MAX_FILENAME and name.endswith(".pdf")


@pytest.mark.parametrize("raw", [
    "a." + "b" * 300,
    "x" * 150 + "." + "y" * 150,
    "y" * 300,
])
def test_a_long_name_never_exceeds_the_limit(raw):
    # What follows the last dot is an extension only if it is short; a long
    # tail is part of the name and is cut like the rest of it.
    name = knowledge.clean_filename(raw)
    assert len(name) == knowledge.MAX_FILENAME
    assert name == raw[:knowledge.MAX_FILENAME]


def test_quota_default_and_override(monkeypatch):
    monkeypatch.delenv(knowledge.QUOTA_ENV, raising=False)
    assert knowledge.quota_bytes() == knowledge.DEFAULT_QUOTA_MB * 1024 * 1024
    monkeypatch.setenv(knowledge.QUOTA_ENV, "250")
    assert knowledge.quota_bytes() == 250 * 1024 * 1024
    monkeypatch.setenv(knowledge.QUOTA_ENV, "2.5")
    assert knowledge.quota_bytes() is None


def test_the_quota_is_read_from_the_partnertype_variable():
    assert knowledge.QUOTA_ENV == "PARTNERTYPE_KNOWLEDGE_QUOTA_MB"


def test_the_operator_command_accepts_exactly_what_the_app_accepts():
    from tools.instance import KNOWLEDGE_QUOTA_RANGE
    assert KNOWLEDGE_QUOTA_RANGE == (1, knowledge.MAX_QUOTA_MB)


def test_content_disposition_is_safe_for_any_name():
    header = knowledge.content_disposition('Cr\u00e8me "br\u00fbl\u00e9e"; \u00e9t\u00e9.pdf')
    ascii_part = header.split(";")[1]
    assert header.startswith("attachment;")
    assert '"' not in ascii_part.split("=", 1)[1].strip('"')
    assert "\r" not in header and "\n" not in header
    assert "filename*=UTF-8''Cr%C3%A8me" in header


def test_content_disposition_falls_back_to_a_plain_name():
    assert knowledge.content_disposition("\u4ef7\u683c.pdf").startswith('attachment; filename="document.pdf"')
    assert knowledge.content_disposition("\u4ef7\u683c").startswith('attachment; filename="document"')


def test_detect_kind_ignores_the_browser_claim():
    assert knowledge.detect_kind(b"%PDF-1.7", "x.docx") == "pdf"
    assert knowledge.detect_kind(b"GIF89a", "x.gif") is None
