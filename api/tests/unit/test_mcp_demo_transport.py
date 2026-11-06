"""The legacy stdio MCP demo never sends its password over a plain network link.

It logs in with an administrator's password, so PARTNERTYPE_URL must be
https://, except on the loopback interface; skipping certificate checks is
allowed only there as well, and redirects are never followed.
"""
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "partnertype_mcp_demo", ROOT / "tools" / "mcp" / "partnertype_mcp.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


@pytest.mark.parametrize("url", [
    "http://firma.example",
    "http://firma.partnertype.com/",
    "http://10.0.0.5:8000",
    "ftp://firma.example",
    "firma.example",
    "https://",
    "https://admin:pw@firma.example",
    "",
])
def test_refuses_anything_but_https_off_loopback(url):
    with pytest.raises(ValueError):
        demo.check_base_url(url)
    with pytest.raises(ValueError):
        demo.PartnerTypeClient(url, "a@b.pl", "secret")


@pytest.mark.parametrize("url", [
    "https://firma.partnertype.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000/",
    "http://[::1]:8000",
])
def test_accepts_https_and_plain_http_on_loopback(url):
    assert demo.check_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", ["https://firma.partnertype.com", "https://192.168.1.10"])
def test_skipping_certificate_checks_is_refused_off_loopback(url):
    with pytest.raises(ValueError):
        demo.check_base_url(url, insecure_tls=True)
    with pytest.raises(ValueError):
        demo.PartnerTypeClient(url, "a@b.pl", "secret", insecure_tls=True)


@pytest.mark.parametrize("url", ["https://localhost:8443", "https://127.0.0.1", "https://[::1]"])
def test_skipping_certificate_checks_is_allowed_on_loopback(url):
    assert demo.check_base_url(url, insecure_tls=True) == url


def test_main_exits_before_reading_stdin_for_plain_http(monkeypatch, capsys):
    monkeypatch.setenv("PARTNERTYPE_URL", "http://firma.example")
    monkeypatch.setenv("PARTNERTYPE_EMAIL", "a@b.pl")
    monkeypatch.setenv("PARTNERTYPE_PASSWORD", "secret")
    monkeypatch.delenv("PARTNERTYPE_INSECURE_TLS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'))

    with pytest.raises(SystemExit) as caught:
        demo.main()

    assert caught.value.code == 2
    assert "https" in capsys.readouterr().err


def test_redirects_are_not_followed():
    handler = demo._NoRedirect()
    request = demo.urllib.request.Request("https://firma.example/api/auth/login")
    assert handler.redirect_request(request, None, 302, "Found", {},
                                    "http://elsewhere.example/") is None
    client = demo.PartnerTypeClient("https://firma.example", "a@b.pl", "secret")
    assert any(isinstance(h, demo._NoRedirect) for h in client.opener.handlers)


def test_docstring_says_it_bypasses_the_gateway_and_is_local_only():
    doc = demo.__doc__
    assert "BYPASSES EVERY CONTROL OF THE API GATEWAY" in doc
    assert "local use only" in doc
    assert "/mcp" in doc
    assert "Fields are minimised" not in doc


def test_main_announces_on_stderr_that_it_bypasses_the_gateway(monkeypatch, capsys):
    monkeypatch.setenv("PARTNERTYPE_URL", "http://localhost:8000")
    monkeypatch.setenv("PARTNERTYPE_EMAIL", "a@b.pl")
    monkeypatch.setenv("PARTNERTYPE_PASSWORD", "secret")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    demo.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "bypasses the API gateway" in captured.err
