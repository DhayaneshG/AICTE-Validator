"""MOCK PKI + signed synthetic PDFs (M7).

    python -m data.generator.signing

Creates a synthetic root CA (the MOCK trust root; its public certificate is the trust anchor shipped in backend/mock/trust_root),
signer certificates, and 12 signed PDFs: 8 valid and 4 negatives (tampered after signing, untrusted signer, expired certificate,
signer not authorised). No real keys, certificates or authorities are involved. Private keys live in data/synthetic/signing_keys
and are synthetic.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import random
from pathlib import Path

import pymupdf
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers
from pyhanko.sign.fields import SigFieldSpec

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "data" / "synthetic"
KEYS = SYN / "signing_keys"
OUT = SYN / "signed"
TRUST = ROOT / "backend" / "mock" / "trust_root"
SEED = 42
UTC = dt.timezone.utc
AUTH_ORG = "MOCK Issuing Authority (synthetic)"


def _key(seed_label: str):
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str, org: str):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, "IN")])


def _cert(subject, issuer, pub, signer_key, not_before, not_after, serial, ca=False, issuer_pub=None):
    b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(pub).serial_number(serial)
         .not_valid_before(not_before).not_valid_after(not_after)
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if ca:
        b = b.add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
    else:
        b = b.add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True)
    return b.sign(signer_key, hashes.SHA256())


def _pem(path: Path, obj, key: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if key:
        path.write_bytes(obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    else:
        path.write_bytes(obj.public_bytes(serialization.Encoding.PEM))


def build_pki() -> dict:
    t0 = dt.datetime(2024, 1, 1, tzinfo=UTC)
    far = dt.datetime(2040, 1, 1, tzinfo=UTC)
    rk = _key("root")
    root = _cert(_name("MOCK AICTE Demo Root CA", AUTH_ORG), _name("MOCK AICTE Demo Root CA", AUTH_ORG), rk.public_key(), rk, t0, far, 1, ca=True)
    _pem(TRUST / "root_ca.pem", root)  # the trust anchor used by the verifier
    pki = {}
    specs = {
        "valid": (rk, root, _name("MOCK Issuer Signer", AUTH_ORG), dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2035, 1, 1, tzinfo=UTC)),
        "expired": (rk, root, _name("MOCK Issuer Signer (expired)", AUTH_ORG), dt.datetime(2020, 1, 1, tzinfo=UTC), dt.datetime(2021, 1, 1, tzinfo=UTC)),
        "unauthorised": (rk, root, _name("Unrelated Vendor Signer", "Unrelated Vendor (synthetic)"), dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2035, 1, 1, tzinfo=UTC)),
    }
    for i, (name, (ik, icert, subj, nb, na)) in enumerate(specs.items(), start=10):
        k = _key(name)
        c = _cert(subj, icert.subject, k.public_key(), ik, nb, na, i)
        _pem(KEYS / f"{name}.key.pem", k, key=True)
        _pem(KEYS / f"{name}.cert.pem", c)
        pki[name] = (k, c)
    # untrusted: its own CA that is NOT shipped in the trust root
    uk = _key("untrusted-ca")
    uroot = _cert(_name("Self-made Unknown CA", "Unknown (synthetic)"), _name("Self-made Unknown CA", "Unknown (synthetic)"), uk.public_key(), uk, t0, far, 2, ca=True)
    sk = _key("untrusted")
    sc = _cert(_name("MOCK Issuer Signer", AUTH_ORG), uroot.subject, sk.public_key(), uk, dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2035, 1, 1, tzinfo=UTC), 20)
    _pem(KEYS / "untrusted.key.pem", sk, key=True)
    _pem(KEYS / "untrusted.cert.pem", sc)
    _pem(KEYS / "untrusted_ca.cert.pem", uroot)
    pki["untrusted"] = (sk, sc)
    _pem(KEYS / "root_ca.cert.pem", root)
    return pki


def _signer(name: str) -> signers.SimpleSigner:
    chain = [KEYS / "untrusted_ca.cert.pem"] if name == "untrusted" else [KEYS / "root_ca.cert.pem"]
    return signers.SimpleSigner.load(str(KEYS / f"{name}.key.pem"), str(KEYS / f"{name}.cert.pem"), ca_chain_files=[str(c) for c in chain])


def sign_pdf(data: bytes, signer_name: str, reason: str = "MOCK issuance") -> bytes:
    w = IncrementalPdfFileWriter(io.BytesIO(data))
    out = io.BytesIO()
    signers.sign_pdf(w, signers.PdfSignatureMetadata(field_name="Signature1", reason=reason, md_algorithm="sha256"),
                     signer=_signer(signer_name), output=out)
    return out.getvalue()


def tamper_after_signing(signed: bytes) -> bytes:
    """Append an incremental update that overwrites a visible value after the signature was applied."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.pdf"
        f.write_bytes(signed)
        doc = pymupdf.open(f)
        page = doc[0]
        w = [w for w in page.get_text("words") if any(c.isdigit() for c in w[4]) and len(w[4]) >= 3][0]
        page.add_redact_annot(pymupdf.Rect(w[:4]), text="9999", fill=(1, 1, 1))
        page.apply_redactions()
        doc.saveIncr()
        doc.close()
        return f.read_bytes()


def main() -> None:
    random.seed(SEED)
    pki = build_pki()
    labels = json.loads((SYN / "labels.json").read_text())["applications"]
    clean = sorted([a for a in labels if a["label"] == "clean"], key=lambda a: a["application_id"])
    # source documents: the first digital PDF of clean applications (alternating dev/test), 12 in total
    srcs = []
    for a in clean:
        d = a["documents"][0]
        srcs.append((a, d, SYN / "applications" / a["application_id"] / d["filename"]))
    dev = [s for s in srcs if s[0]["split"] == "dev"][:6]
    test = [s for s in srcs if s[0]["split"] == "test"][:6]
    plan = [  # (kind, split, expected)
        ("valid", dev[0]), ("valid", dev[1]), ("valid", dev[2]), ("valid", dev[3]),
        ("valid", test[0]), ("valid", test[1]), ("valid", test[2]), ("valid", test[3]),
        ("tampered_after_signing", dev[4]), ("untrusted_signer", dev[5]),
        ("expired_certificate", test[4]), ("signer_not_authorised", test[5]),
    ]
    items = []
    for i, (kind, (a, d, p)) in enumerate(plan, start=1):
        data = p.read_bytes()
        if kind == "valid":
            signed = sign_pdf(data, "valid")
        elif kind == "tampered_after_signing":
            signed = tamper_after_signing(sign_pdf(data, "valid"))
        elif kind == "untrusted_signer":
            signed = sign_pdf(data, "untrusted")
        elif kind == "expired_certificate":
            signed = sign_pdf(data, "expired")
        else:
            signed = sign_pdf(data, "unauthorised")
        fn = f"sig_{i:02d}_{kind}.pdf"
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / fn).write_bytes(signed)
        items.append({"file": fn, "kind": kind, "expected_status": {"valid": "valid", "tampered_after_signing": "modified_after_signing"}.get(kind, kind), "split": a["split"],
                      "source": str(p.relative_to(SYN)), "application_id": a["application_id"], "doc_type": d["doc_type"],
                      "sha256": hashlib.sha256(signed).hexdigest()})
    (OUT / "labels.json").write_text(json.dumps({"note": "Synthetic signed PDFs (MOCK trust root).", "items": items}, indent=1))
    print(len(items), "signed PDFs written")


if __name__ == "__main__":
    main()
