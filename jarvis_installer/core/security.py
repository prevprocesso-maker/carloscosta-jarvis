from __future__ import annotations

import hashlib
import hmac
import base64
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class SignatureResult:
    valid: bool
    algorithm: str
    expected: str
    actual: str
    message: str = ""


def compute_sha256(file_path: Path) -> str:
    """Computa SHA256 de arquivo."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_checksum(file_path: Path, expected_sha256: str) -> SignatureResult:
    """Verifica SHA256 de arquivo."""
    actual = compute_sha256(file_path)
    valid = hmac.compare_digest(actual.lower(), expected_sha256.lower())
    return SignatureResult(
        valid=valid,
        algorithm="SHA256",
        expected=expected_sha256,
        actual=actual,
        message="Checksum OK" if valid else "Checksum mismatch"
    )


def verify_signature(file_path: Path, signature_b64: str, public_key_pem: str) -> SignatureResult:
    """Verifica assinatura RSA/PSS de arquivo."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.exceptions import InvalidSignature
        
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        signature = base64.b64decode(signature_b64)
        
        with open(file_path, "rb") as f:
            data = f.read()
        
        public_key.verify(
            signature,
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return SignatureResult(valid=True, algorithm="RSA-PSS-SHA256", expected="", actual="", message="Signature valid")
    except Exception as e:
        return SignatureResult(valid=False, algorithm="RSA-PSS-SHA256", expected="", actual="", message=str(e))


def generate_checksums(file_paths: list[Path]) -> Dict[str, str]:
    """Gera dicionário de checksums para múltiplos arquivos."""
    return {str(p): compute_sha256(p) for p in file_paths if p.exists()}


def save_checksums(checksums: Dict[str, str], output_path: Path) -> None:
    import json
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(checksums, f, indent=2)


def load_checksums(checksums_path: Path) -> Dict[str, str]:
    import json
    if not checksums_path.exists():
        return {}
    with open(checksums_path) as f:
        return json.load(f)


def verify_all_checksums(checksums: Dict[str, str], base_dir: Path = Path(".")) -> Dict[str, SignatureResult]:
    results = {}
    for rel_path, expected in checksums.items():
        file_path = base_dir / rel_path
        if file_path.exists():
            results[rel_path] = verify_checksum(file_path, expected)
        else:
            results[rel_path] = SignatureResult(
                valid=False, algorithm="SHA256", expected="", actual="",
                message="File not found"
            )
    return results


def sign_file(file_path: Path, private_key_pem: str) -> str:
    """Gera assinatura base64 de arquivo."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    
    with open(file_path, "rb") as f:
        data = f.read()
    
    signature = private_key.sign(
        data,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def verify_cosign_signature(file_path: Path, sig_path: Path, cert_path: Path) -> bool:
    """Verifica assinatura cosign (keyless)."""
    try:
        result = subprocess.run([
            "cosign", "verify-blob",
            "--certificate", str(cert_path),
            "--signature", str(sig_path),
            str(file_path)
        ], capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception:
        return False


def generate_sbom(output_path: Path) -> bool:
    """Gera SBOM (Software Bill of Materials) via syft."""
    try:
        result = subprocess.run([
            "syft", "dir:.", "-o", f"spdx-json={output_path}"
        ], capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception:
        return False