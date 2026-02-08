"""Shared encryption/decryption for JWPUB files.

Algorithm:
    1. Build string: {MepsLanguageIndex}_{Symbol}_{Year} (+ _{IssueTagNumber} if non-zero)
    2. SHA-256 hash of that string
    3. XOR with constant: 11cbb5587e32846d4c26790c633da289f66fe5842a3a585ce1bc3a294af5ada7
    4. First 16 bytes = AES-128-CBC key, Last 16 bytes = IV
    5. Content is zlib-compressed then AES encrypted
"""
from __future__ import annotations

import binascii
import hashlib
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding

XOR_KEY_HEX = "11cbb5587e32846d4c26790c633da289f66fe5842a3a585ce1bc3a294af5ada7"


def hex_to_bytes(hex_str: str) -> bytes:
    return binascii.unhexlify(hex_str)


def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def compute_publication_card_hash(
    meps_language_index: int,
    symbol: str,
    year: int,
    issue_tag_number: int = 0,
) -> str:
    parts = [str(meps_language_index), str(symbol), str(year)]
    if issue_tag_number != 0:
        parts.append(str(issue_tag_number))
    pub_string = "_".join(parts)

    digest = hashlib.sha256(pub_string.encode("utf-8")).digest()
    xor_key = hex_to_bytes(XOR_KEY_HEX)
    card_hash = xor_bytes(digest, xor_key)
    return card_hash.hex()


def get_key_iv(meps_language_index: int, symbol: str, year: int, issue_tag_number: int = 0) -> tuple[bytes, bytes]:
    full_hash_hex = compute_publication_card_hash(meps_language_index, symbol, year, issue_tag_number)
    full_hash = hex_to_bytes(full_hash_hex)
    return full_hash[:16], full_hash[16:32]


# --- Decryption ---

def aes128_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def zlib_inflate(data: bytes) -> bytes:
    return zlib.decompress(data)


def decrypt(blob: bytes, meps_language_index: int, symbol: str, year: int, issue_tag_number: int = 0) -> str:
    key, iv = get_key_iv(meps_language_index, symbol, year, issue_tag_number)
    decrypted = aes128_cbc_decrypt(key, iv, blob)
    inflated = zlib_inflate(decrypted)
    return inflated.decode("utf-8", errors="replace")


# --- Encryption ---

def aes128_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    # PKCS7 padding
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def zlib_deflate(data: bytes) -> bytes:
    return zlib.compress(data)


def encrypt(content: str, meps_language_index: int, symbol: str, year: int, issue_tag_number: int = 0) -> bytes:
    key, iv = get_key_iv(meps_language_index, symbol, year, issue_tag_number)
    compressed = zlib_deflate(content.encode("utf-8"))
    return aes128_cbc_encrypt(key, iv, compressed)
