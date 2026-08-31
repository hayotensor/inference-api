"""Cert-pinned client: live leaf-cert fingerprint pin over a real self-signed TLS server.

We stand up a throwaway HTTPS server with a self-signed cert (CA/hostname verification
would normally reject it), then:
  * a client pinned to that cert's sha256(DER) connects and gets a response;
  * a client pinned to a DIFFERENT fingerprint raises CertFingerprintMismatch;
  * a bearer token is attached as an Authorization header;
  * build_pinned_client refuses a non-ok verification result.
"""

from __future__ import annotations

import datetime
import hashlib
import http.server
import ssl
import threading

import pytest

from talaris_attest import (
    CertFingerprintMismatch,
    VerificationResult,
    build_pinned_client,
)


def _gen_self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tee-wrapper")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2100, 1, 1, tzinfo=datetime.timezone.utc))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    cert_path = tmp_path / "s_cert.pem"
    key_path = tmp_path / "s_key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    fpr = hashlib.sha256(der).hexdigest()
    return str(cert_path), str(key_path), fpr


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def tls_server(tmp_path):
    cert_path, key_path, fpr = _gen_self_signed(tmp_path)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{port}", fpr
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _ok_result(fpr: str) -> VerificationResult:
    from talaris_attest import CheckResult
    res = VerificationResult(tls_cert_fingerprint=fpr)
    res.add(CheckResult("x", True, "ok"))
    return res


def test_pinned_client_accepts_matching_cert(tls_server):
    base_url, fpr = tls_server
    client = build_pinned_client(_ok_result(fpr), base_url)
    try:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        client.close()


def test_pinned_client_rejects_mismatched_cert(tls_server):
    base_url, fpr = tls_server
    wrong = ("0" if fpr[0] != "0" else "1") + fpr[1:]
    client = build_pinned_client(_ok_result(wrong), base_url)
    try:
        with pytest.raises(CertFingerprintMismatch):
            client.get("/")
    finally:
        client.close()


def test_pinned_client_attaches_bearer(tls_server):
    base_url, fpr = tls_server
    client = build_pinned_client(_ok_result(fpr), base_url, bearer="sekret")
    try:
        assert client.headers["Authorization"] == "Bearer sekret"
    finally:
        client.close()


def test_pinned_client_refuses_failed_verification(tls_server):
    base_url, fpr = tls_server
    from talaris_attest import CheckResult
    bad = VerificationResult(tls_cert_fingerprint=fpr)
    bad.add(CheckResult("x", False, "nope"))
    with pytest.raises(ValueError):
        build_pinned_client(bad, base_url)
