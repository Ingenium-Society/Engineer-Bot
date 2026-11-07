"""The startup banner is the operator's only map: check both networking modes."""

from __future__ import annotations

from engineer_bot import config
from engineer_bot.__main__ import banner


def lan_settings(**overrides) -> config.Settings:
    return config.Settings(https_cert="certs/cert.pem", https_key="certs/key.pem", **overrides)


def test_lan_mode_prints_the_lan_address_and_local_tls():
    text = banner("0.0.0.0", 8000, lan_settings())
    assert "/team/a" in text
    assert "192.168." in text or "127.0.0.1" in text
    assert "https://localhost:8000/admin" in text
    assert "Jaringan" in text and "lokal" in text
    assert "aktif - percayakan sertifikat" in text


def test_lan_mode_without_a_cert_warns_about_the_microphone():
    text = banner("0.0.0.0", 8000, config.Settings())
    assert "http://localhost:8000/admin" in text
    assert "mikrofon diblokir" in text


def test_tunnel_mode_prints_the_public_url_for_the_teams():
    settings = config.Settings(public_url="https://abc.trycloudflare.com")
    text = banner("0.0.0.0", 8000, settings)
    assert "https://abc.trycloudflare.com/team/a" in text
    assert "https://abc.trycloudflare.com/team/b" in text
    assert "tunnel ->" in text
    assert "ditangani tunnel" in text


def test_tunnel_mode_keeps_the_projector_and_operator_on_localhost():
    settings = config.Settings(public_url="https://abc.trycloudflare.com")
    text = banner("0.0.0.0", 8000, settings)
    assert "http://localhost:8000/screen" in text
    assert "http://localhost:8000/admin" in text


def test_tunnel_mode_serves_plain_http_locally():
    settings = config.Settings(
        public_url="https://abc.trycloudflare.com",
        https_cert="certs/cert.pem",
        https_key="certs/key.pem",
    )
    assert settings.scheme == "http"
    assert settings.tunnel is True
    assert "HTTPS_CERT diabaikan" in banner("0.0.0.0", 8000, settings)


def test_lan_mode_is_the_default():
    settings = config.Settings()
    assert settings.tunnel is False
    assert settings.public_url == ""
    assert settings.scheme == "http"


def test_a_trailing_slash_on_public_url_is_trimmed(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://abc.trycloudflare.com/")
    settings = config.Settings.from_env()
    assert settings.public_url == "https://abc.trycloudflare.com"
    assert "https://abc.trycloudflare.com/team/a" in banner("0.0.0.0", 8000, settings)
