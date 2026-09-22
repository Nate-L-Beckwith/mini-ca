"""Behavioural tests for the mini-ca CA core, watcher and CLI."""
from __future__ import annotations

import os
import stat
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.verification import PolicyBuilder, Store
from typer.testing import CliRunner

import ca_core
from ca_core import CAError, find_cert, load_ca, load_cert, normalize_name, sans_of
from init_ca import init_ca
from issue_cert import issue_cert, renew_cert
from mini_ca import APP
from watch import DomainsHandler, parse_domains

POSIX = os.name != "nt"
runner = CliRunner()


def _verify(leaf: x509.Certificate, ca_cert: x509.Certificate, host: str) -> None:
    PolicyBuilder().store(Store([ca_cert])).build_server_verifier(ca_core.general_name(host)).verify(leaf, [])


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── names ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Blog.Acme.TEST.", "blog.acme.test"),
        ("*.wild.dev", "*.wild.dev"),
        ("nas", "nas"),
        ("192.168.1.10", "192.168.1.10"),
        ("fe80::1", "fe80::1"),
        ("my_host.lan", "my_host.lan"),
    ],
)
def test_normalize_name_accepts(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "", "  ", "*", "foo.*.bar", "-bad.lan", "bad-.lan", "a..b", "bü.lan", "a" * 64 + ".lan",
        # anything path-like must never reach the filesystem layer
        "/data/rootCA/rootCA", "../DOMAINS", "..", "foo/bar.lan", "C:\\evil",
    ],
)
def test_normalize_name_rejects(raw: str) -> None:
    with pytest.raises(CAError):
        normalize_name(raw)


def test_issue_cannot_escape_certificates_dir(ca: Path) -> None:
    root_key_before = (ca / "rootCA.key").read_bytes()
    for evil in ("/data/rootCA/rootCA", "../rootCA/rootCA", "../../etc/passwd"):
        with pytest.raises(CAError):
            issue_cert(evil, [], ca, ca_core.certs_dir())
        with pytest.raises(CAError):
            issue_cert("ok.lan", [evil], ca, ca_core.certs_dir())
    assert (ca / "rootCA.key").read_bytes() == root_key_before
    assert not ca_core.certs_dir().exists()


def test_file_layout_helpers(tmp_path: Path) -> None:
    assert ca_core.short_label("*.example.lan") == "example"
    assert ca_core.short_label("blog.acme.test") == "blog"
    assert ca_core.file_stem("*.example.lan") == "_wildcard.example.lan"
    assert ca_core.file_stem("fe80::1") == "fe80--1"
    assert ca_core.cert_folder("blog.acme.test", tmp_path) == tmp_path / "blog"
    assert ca_core.cert_folder("blog.acme.test", tmp_path, full_path=True) == tmp_path / "blog.acme.test"


# ── root CA ────────────────────────────────────────────────────────────────
def test_init_creates_root(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINICA_CA_NAME", "Acme Lab Root")
    monkeypatch.setenv("MINICA_ROOT_DAYS", "100")
    cert = init_ca(ca_core.ca_dir())
    assert cert is not None
    key_file, cert_file = ca_core.ca_dir() / "rootCA.key", ca_core.ca_dir() / "rootCA.crt"
    assert key_file.is_file() and cert_file.is_file()
    if POSIX:
        assert _mode(key_file) == 0o600
    assert ca_core.common_name(cert) == "Acme Lab Root"
    assert cert.not_valid_before_utc < ca_core.utcnow()
    assert 99 <= ca_core.days_left(cert) <= 100
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical and bc.value.ca and bc.value.path_length == 0
    ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.key_cert_sign and ku.crl_sign
    cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    key, loaded = load_ca(ca_core.ca_dir())
    assert loaded == cert


def test_init_is_idempotent_and_force_keeps_backup(ca: Path) -> None:
    before = load_cert(ca / "rootCA.crt")
    assert init_ca(ca) is None
    assert load_cert(ca / "rootCA.crt") == before
    after = init_ca(ca, force=True, days=30)
    assert after is not None and after != before
    backups = sorted(ca.glob("rootCA.*.bak-*"))
    assert len(backups) == 2
    assert load_cert(next(b for b in backups if ".crt." in b.name)) == before


def test_load_ca_missing_gives_readable_error(data: Path) -> None:
    with pytest.raises(CAError, match="run 'mini_ca.py init'"):
        load_ca(ca_core.ca_dir())


# ── leaf certificates ──────────────────────────────────────────────────────
def test_issue_basic(ca: Path) -> None:
    _, ca_cert = load_ca(ca)
    crt = issue_cert("blog.acme.test", ["www.blog.acme.test"], ca, ca_core.certs_dir())
    folder = ca_core.certs_dir() / "blog"
    assert crt == folder / "blog.acme.test.crt"
    assert (folder / "blog.acme.test.key").is_file()
    chain = folder / "blog.acme.test.fullchain.crt"
    assert chain.read_bytes().count(b"BEGIN CERTIFICATE") == 2
    if POSIX:
        assert _mode(folder / "blog.acme.test.key") == 0o600
        assert _mode(crt) & 0o044  # public material stays readable

    leaf = load_cert(crt)
    assert ca_core.common_name(leaf) == "blog.acme.test"
    assert sans_of(leaf) == ["blog.acme.test", "www.blog.acme.test"]
    assert 824 <= ca_core.days_left(leaf) <= 825
    assert not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku and ExtendedKeyUsageOID.CLIENT_AUTH in eku
    aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    assert aki.key_identifier == ski.digest
    _verify(leaf, ca_cert, "blog.acme.test")
    _verify(leaf, ca_cert, "www.blog.acme.test")


def test_issue_ip_addresses_become_ip_sans(ca: Path) -> None:
    _, ca_cert = load_ca(ca)
    leaf = load_cert(issue_cert("nas.lan", ["192.168.1.10", "fe80::1"], ca, ca_core.certs_dir()))
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["nas.lan"]
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["192.168.1.10", "fe80::1"]
    _verify(leaf, ca_cert, "192.168.1.10")

    crt = issue_cert("10.0.0.5", [], ca, ca_core.certs_dir())
    assert crt == ca_core.certs_dir() / "10.0.0.5" / "10.0.0.5.crt"
    _verify(load_cert(crt), ca_cert, "10.0.0.5")


def test_issue_wildcard_uses_windows_safe_names(ca: Path) -> None:
    _, ca_cert = load_ca(ca)
    crt = issue_cert("*.Wild.dev", ["api.other.dev"], ca, ca_core.certs_dir())
    assert crt == ca_core.certs_dir() / "wild" / "_wildcard.wild.dev.crt"
    leaf = load_cert(crt)
    assert ca_core.common_name(leaf) == "*.wild.dev"
    _verify(leaf, ca_cert, "anything.wild.dev")
    _verify(leaf, ca_cert, "api.other.dev")
    assert find_cert("*.wild.dev", ca_core.certs_dir()) == (crt, False)


def test_issue_full_path_and_custom_days(ca: Path) -> None:
    crt = issue_cert("app.example.com", [], ca, ca_core.certs_dir(), full_path=True, days=10)
    assert crt == ca_core.certs_dir() / "app.example.com" / "app.example.com.crt"
    assert 9 <= ca_core.days_left(load_cert(crt)) <= 10
    assert find_cert("app.example.com", ca_core.certs_dir()) == (crt, True)


def test_issue_long_name_omits_cn_and_marks_san_critical(ca: Path) -> None:
    _, ca_cert = load_ca(ca)
    long_name = ".".join(["a" * 40, "b" * 40, "lan"])
    leaf = load_cert(issue_cert(long_name, [], ca, ca_core.certs_dir()))
    assert len(leaf.subject) == 0
    assert leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).critical
    _verify(leaf, ca_cert, long_name)


def test_validity_is_exact_and_backdated(ca: Path) -> None:
    leaf = load_cert(issue_cert("exact.lan", [], ca, ca_core.certs_dir(), days=10))
    # back-dating for clock skew must not stretch the total validity (Apple's 825-day ceiling)
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc == timedelta(days=10)
    assert leaf.not_valid_before_utc < ca_core.utcnow() - timedelta(minutes=30)
    root = load_cert(ca / "rootCA.crt")
    assert root.not_valid_after_utc - root.not_valid_before_utc == timedelta(days=3650)


def test_issue_is_clamped_to_ca_expiry_and_refuses_expired_ca(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_ca(ca_core.ca_dir(), days=2)
    root = load_cert(ca_core.ca_dir() / "rootCA.crt")
    leaf = load_cert(issue_cert("short.lan", [], ca_core.ca_dir(), ca_core.certs_dir()))
    assert leaf.not_valid_after_utc == root.not_valid_after_utc

    issue_mod = sys.modules["issue_cert"]
    monkeypatch.setattr(issue_mod, "utcnow", lambda: ca_core.utcnow() + timedelta(days=3))
    with pytest.raises(CAError, match="expired"):
        issue_cert("late.lan", [], ca_core.ca_dir(), ca_core.certs_dir())


def test_issue_rejects_bad_input(ca: Path) -> None:
    with pytest.raises(CAError):
        issue_cert("foo.*.bar", [], ca, ca_core.certs_dir())
    with pytest.raises(CAError):
        issue_cert("ok.lan", ["-nope.lan"], ca, ca_core.certs_dir())
    with pytest.raises(CAError):
        issue_cert("ok.lan", [], ca, ca_core.certs_dir(), days=0)


def test_renew_keeps_sans_and_folder(ca: Path) -> None:
    first = load_cert(issue_cert("shop.lan", ["www.shop.lan", "10.1.1.1"], ca, ca_core.certs_dir(), full_path=True))
    renewed_path = renew_cert("shop.lan", ca, ca_core.certs_dir(), days=400)
    renewed = load_cert(renewed_path)
    assert renewed_path == ca_core.certs_dir() / "shop.lan" / "shop.lan.crt"
    assert renewed.serial_number != first.serial_number
    assert renewed.public_key() != first.public_key()
    assert sans_of(renewed) == sans_of(first)
    assert 399 <= ca_core.days_left(renewed) <= 400
    with pytest.raises(CAError, match="use 'issue' first"):
        renew_cert("never.lan", ca, ca_core.certs_dir())


@pytest.mark.skipif(not POSIX, reason="'*' is not a legal file name on Windows")
def test_find_cert_recognises_legacy_wildcard_files(ca: Path) -> None:
    folder = ca_core.certs_dir() / "old"
    folder.mkdir(parents=True)
    (folder / "*.old.lan.crt").write_text("x")
    (folder / "*.old.lan.key").write_text("x")
    assert find_cert("*.old.lan", ca_core.certs_dir()) == (folder / "*.old.lan.crt", False)


# ── watcher ────────────────────────────────────────────────────────────────
def test_parse_domains() -> None:
    text = "# comment\n\n  blog.lan   \nnas.lan 192.168.1.10 www.nas.lan # trailing\n*.wild.dev\n"
    assert parse_domains(text) == [
        ("blog.lan", [], "blog.lan"),
        ("nas.lan", ["192.168.1.10", "www.nas.lan"], "nas.lan 192.168.1.10 www.nas.lan # trailing"),
        ("*.wild.dev", [], "*.wild.dev"),
    ]


def test_watcher_is_idempotent_and_skips_bad_lines(ca: Path) -> None:
    domains = ca_core.domains_file()
    domains.write_text("blog.lan\nbad_..name\n# nas.lan\nnas.lan 192.168.1.10\n")
    handler = DomainsHandler(domains, ca, ca_core.certs_dir())
    handler.process()
    blog = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")
    nas = load_cert(ca_core.certs_dir() / "nas" / "nas.lan.crt")
    assert sans_of(nas) == ["nas.lan", "192.168.1.10"]
    assert not (ca_core.certs_dir() / "bad_").exists()

    handler.process()  # nothing changed: no re-issue, keys stay put
    assert load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt") == blog
    assert load_cert(ca_core.certs_dir() / "nas" / "nas.lan.crt") == nas

    fresh = DomainsHandler(domains, ca, ca_core.certs_dir())  # a restart must not rotate keys either
    fresh.process()
    assert load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt") == blog


def test_watcher_reissues_for_new_sans_and_expiry(ca: Path) -> None:
    domains = ca_core.domains_file()
    domains.write_text("blog.lan\n")
    handler = DomainsHandler(domains, ca, ca_core.certs_dir(), renew_within=30)
    handler.process()
    first = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")

    domains.write_text("blog.lan www.blog.lan\n")
    handler.process()
    second = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")
    assert second != first and sans_of(second) == ["blog.lan", "www.blog.lan"]

    issue_cert("blog.lan", ["www.blog.lan"], ca, ca_core.certs_dir(), days=5)  # about to expire
    handler.process()
    third = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")
    assert third != second and ca_core.days_left(third) > 800


def test_watcher_reissues_after_ca_rotation(ca: Path) -> None:
    domains = ca_core.domains_file()
    domains.write_text("blog.lan\n")
    handler = DomainsHandler(domains, ca, ca_core.certs_dir())
    handler.process()
    first = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")

    init_ca(ca, force=True, days=30)
    _, new_root = load_ca(ca)
    assert not ca_core.is_issued_by(first, new_root)
    handler.process()
    second = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")
    assert second != first and ca_core.is_issued_by(second, new_root)
    handler.process()
    assert load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt") == second


def test_watcher_reacts_only_to_completed_writes(ca: Path) -> None:
    """Our own read of DOMAINS emits opened/closed-no-write events; they must not re-trigger."""
    from watchdog import events as ev

    domains = ca_core.domains_file()
    domains.write_text("")
    handler = DomainsHandler(domains, ca, ca_core.certs_dir())
    calls: list[int] = []
    handler.process = lambda: calls.append(1)  # type: ignore[method-assign]
    path, parent = str(domains), str(domains.parent)

    ignored = [ev.FileOpenedEvent(path), ev.DirModifiedEvent(parent), ev.FileModifiedEvent(str(domains.parent / "other"))]
    if hasattr(ev, "FileClosedNoWriteEvent"):
        ignored.append(ev.FileClosedNoWriteEvent(path))
    for event in ignored:
        handler.on_any_event(event)
    assert calls == []

    for event in (
        ev.FileModifiedEvent(path),
        ev.FileCreatedEvent(path),
        ev.FileClosedEvent(path),
        ev.FileMovedEvent(str(domains.parent / ".DOMAINS.swp"), path),
    ):
        handler.on_any_event(event)
    assert len(calls) == 4


def test_watcher_survives_missing_ca(data: Path) -> None:
    domains = ca_core.domains_file()
    domains.write_text("blog.lan\n")
    DomainsHandler(domains, ca_core.ca_dir(), ca_core.certs_dir()).process()  # must not raise
    assert not ca_core.certs_dir().exists()


# ── CLI ────────────────────────────────────────────────────────────────────
def test_cli_end_to_end(data: Path) -> None:
    assert runner.invoke(APP, ["--version"]).output.startswith("mini-ca ")

    failing = runner.invoke(APP, ["issue", "blog.lan"])
    assert failing.exit_code == 1 and "root CA not found" in failing.output

    assert runner.invoke(APP, ["init", "--days", "30"]).exit_code == 0
    assert "already present" in runner.invoke(APP, ["init"]).output

    result = runner.invoke(APP, ["issue", "blog.lan", "--san", "www.blog.lan", "--san", "10.0.0.1"])
    assert result.exit_code == 0, result.output
    assert runner.invoke(APP, ["issue", "shop.lan", "--full-path", "--days", "3"]).exit_code == 0

    listed = runner.invoke(APP, ["list"])
    assert listed.exit_code == 0 and "blog.lan" in listed.output and "RENEW SOON" in listed.output
    as_json = runner.invoke(APP, ["list", "--json"])
    assert as_json.exit_code == 0 and '"days_left"' in as_json.output

    info = runner.invoke(APP, ["info"])
    assert info.exit_code == 0 and "CN=mini-ca root" in info.output and "sha256" in info.output

    assert runner.invoke(APP, ["verify", "blog.lan"]).exit_code == 0
    assert runner.invoke(APP, ["verify", "10.0.0.1", "--cert", str(ca_core.certs_dir() / "blog" / "blog.lan.crt")]).exit_code == 0
    wrong = runner.invoke(APP, ["verify", "other.lan", "--cert", str(ca_core.certs_dir() / "blog" / "blog.lan.crt")])
    assert wrong.exit_code == 1 and "NOT valid" in wrong.output

    before = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")
    assert runner.invoke(APP, ["renew", "blog.lan"]).exit_code == 0
    after = load_cert(ca_core.certs_dir() / "blog" / "blog.lan.crt")
    assert after != before and sans_of(after) == sans_of(before)

    assert runner.invoke(APP, ["add", "nas.lan", "--san", "192.168.1.10"]).exit_code == 0
    assert "already listed" in runner.invoke(APP, ["add", "NAS.lan"]).output
    assert runner.invoke(APP, ["add", "not valid!"]).exit_code == 1
    assert ca_core.domains_file().read_text() == "nas.lan 192.168.1.10\n"

    assert runner.invoke(APP, ["issue", "bad_..name"]).exit_code == 1

    rotated = runner.invoke(APP, ["init", "--force", "--days", "30"])
    assert rotated.exit_code == 0 and "kept previous" in rotated.output
    assert "OLD CA" in runner.invoke(APP, ["list"]).output
    assert runner.invoke(APP, ["verify", "blog.lan"]).exit_code == 1
