"""
The producer's own name and logo (api/branding.py) and the e-mail layout that
carries them (api/email_layout.py, api/email_service.py).

The logo is served to anybody who opens the login page, so what goes in is
checked by its first bytes, not by what the browser said it was.
"""
import base64
import email

import pytest

from api import branding, email_layout, email_service
from api.settings_api import get_all
from api.tests.unit.test_settings import _seed_setting


def _png(width=64, height=64, extra=0):
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big")
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + ihdr + b"\x08\x06\x00\x00\x00" + b"\x00" * extra


def _b64(data):
    return base64.b64encode(data).decode()


class TestSaving:
    def test_a_png_is_kept_and_announced_with_a_version(self, admin_user):
        info = branding.save_logo(_b64(_png()), admin_user)
        assert info["logo_version"]
        assert branding.logo() == (_png(), "png")

    def test_a_data_url_from_the_browser_is_accepted(self, admin_user):
        branding.save_logo("data:image/jpeg;base64," + _b64(b"\xff\xd8\xff\xe0" + b"\x00" * 20), admin_user)
        assert branding.logo()[1] == "jpeg"

    @pytest.mark.parametrize("data", [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"GIF89a" + b"\x00" * 20,
        b"<html>not an image</html>",
    ])
    def test_anything_but_png_or_jpeg_is_refused(self, admin_user, data):
        with pytest.raises(ValueError, match="PNG lub JPG"):
            branding.save_logo(_b64(data), admin_user)
        assert branding.logo() is None

    def test_a_file_over_the_limit_is_refused(self, admin_user):
        with pytest.raises(ValueError, match="200 KB"):
            branding.save_logo(_b64(_png(extra=branding.MAX_LOGO_BYTES)), admin_user)

    def test_a_huge_picture_is_refused(self, admin_user):
        with pytest.raises(ValueError, match="pikseli"):
            branding.save_logo(_b64(_png(width=5000)), admin_user)

    def test_broken_base64_is_refused(self, admin_user):
        with pytest.raises(ValueError):
            branding.save_logo("!!!not base64!!!", admin_user)

    def test_only_somebody_who_configures_the_system_may_change_it(self, partner_user):
        with pytest.raises(PermissionError):
            branding.save_logo(_b64(_png()), partner_user)
        with pytest.raises(PermissionError):
            branding.remove_logo(partner_user)

    def test_removing_brings_back_no_logo(self, admin_user):
        branding.save_logo(_b64(_png()), admin_user)
        assert branding.remove_logo(admin_user)["logo_version"] is None
        assert branding.logo() is None


class TestReading:
    def test_the_logo_never_travels_with_the_settings(self, admin_user):
        branding.save_logo(_b64(_png()), admin_user)
        assert not [key for key in get_all() if key.startswith("brand_")]

    def test_the_old_default_name_is_nobodys_brand(self, admin_user):
        assert branding.public_info()["company_name"] is None
        _seed_setting("company_name", "Acme")
        assert branding.public_info()["company_name"] == "Acme"

    def test_a_new_logo_gets_a_new_version(self, admin_user):
        first = branding.save_logo(_b64(_png()), admin_user)["logo_version"]
        second = branding.save_logo(_b64(_png(width=32)), admin_user)["logo_version"]
        assert first != second


class TestMail:
    def test_the_producers_logo_goes_into_the_mail(self, admin_user, mocker):
        branding.save_logo(_b64(_png()), admin_user)
        _seed_setting("company_name", "Acme")
        mocker.patch.object(email_service, "_get_smtp_config", return_value={
            "host": "mail", "port": 1025, "user": "", "passwd": "", "from": "noreply@test.pl",
            "enabled": True, "capture": True})
        smtp = mocker.patch("api.email_service.smtplib.SMTP")
        assert email_service.send_password_reset("jan@firma.pl", "Jan", "https://acme.test/x?token=abc")["sent"]
        raw = smtp.return_value.__enter__.return_value.sendmail.call_args[0][2]
        message = email.message_from_string(raw)
        images = [part for part in message.walk() if part.get_content_maintype() == "image"]
        assert images and images[0].get_payload(decode=True) == _png()
        assert images[0]["Content-ID"] == f"<{email_layout.LOGO_CID}>"
        html = next(part for part in message.walk() if part.get_content_type() == "text/html")
        body = html.get_payload(decode=True).decode()
        assert "cid:logo" in body and "Acme" in body and "https://acme.test/x?token=abc" in body


def test_the_button_escapes_its_address():
    html = email_layout.button("Ustaw hasło", 'https://x.test/"><script>')
    assert "<script>" not in html and "&quot;&gt;&lt;script&gt;" in html


class TestPartnerTerms:
    """The producer's own terms, linked from the partner sign-up form (ledger #57)."""

    def test_an_address_is_saved_trimmed_and_shown_on_the_public_page(self, admin_user):
        from api.settings_api import set_many
        set_many({"partner_terms_url": "  https://acme.pl/regulamin  "}, admin_user)
        assert branding.public_info()["partner_terms_url"] == "https://acme.pl/regulamin"

    def test_no_address_means_no_link(self, admin_user):
        assert branding.public_info()["partner_terms_url"] is None

    @pytest.mark.parametrize("value", [
        "javascript:alert(1)",
        "acme.pl/regulamin",
        'https://acme.pl/" onmouseover="x',
        "https://acme.pl/" + "a" * 600,
    ])
    def test_anything_but_a_web_address_is_refused(self, admin_user, value):
        from api.settings_api import set_many
        from api.validation import ValidationError
        with pytest.raises(ValidationError, match="Adres regulaminu"):
            set_many({"partner_terms_url": value}, admin_user)

    def test_clearing_the_address_removes_the_link(self, admin_user):
        from api.settings_api import set_many
        set_many({"partner_terms_url": "https://acme.pl/regulamin"}, admin_user)
        set_many({"partner_terms_url": ""}, admin_user)
        assert branding.public_info()["partner_terms_url"] is None
