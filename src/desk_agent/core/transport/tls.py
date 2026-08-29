"""IPC 传输层 TLS：自签证书生成 + 服务端/客户端 SSLContext。

本地守护进程不需要公共 CA，采用「自签证书 + 证书固定（pinning）」：
- core 每次启动生成一次性自签证书（ECDSA P-256），私钥 0600 落盘；
- 证书 SHA-256 指纹写入 run 文件，客户端以该证书为唯一信任锚校验；
- 任何其他证书（MITM 攻击者的）都会在校验阶段被拒绝。

见 docs/COMMUNICATION_SECURITY.md。
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import ssl
from pathlib import Path


# 生成自签证书（SAN 含传入 hosts），返回证书 DER 的 SHA-256 指纹（hex）
def generate_self_signed(
    hosts: list[str],
    key_path: Path,
    cert_path: Path,
    *,
    validity_days: int = 3650,
) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "desk-core")])

    sans: list[x509.GeneralName] = []
    for h in hosts:
        if h in ("0.0.0.0", "::"):
            continue
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))
    # 默认主机名兜底，保证默认配置下主机名校验可用
    if not sans:
        sans = [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]

    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_cert_sign=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
                crl_sign=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if os.name != "nt":
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o600)
    return cert.fingerprint(hashes.SHA256()).hex()


# 服务端 SSLContext：加载自签证书链
def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


# 客户端 SSLContext：以传入的自签证书 PEM 为唯一信任锚（证书固定）
def client_context(cert_pem: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # 信任锚即固定证书本身，无需主机名匹配
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_verify_locations(cadata=cert_pem)
    return ctx
