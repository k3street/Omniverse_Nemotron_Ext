"""Phase 8b — tests for the determinism harness.

Three invariants from the spec:
  (i)   `derive_seed(root, *parts)` is idempotent — same inputs always
        produce the same int.
  (ii)  Any byte change in any input changes the seed.
  (iii) `DeterminismToken.to_hex()` ↔ `from_hex()` round-trip is identity.

Plus auxiliary tests for content_hash determinism and edge cases.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.utils.determinism import (
    DeterminismToken,
    content_hash,
    derive_seed,
    make_token,
)


# ---------------------------------------------------------------------------
# derive_seed


def test_derive_seed_idempotent():
    """(i) Same inputs → same seed, every time."""
    assert derive_seed(42, b"a", b"b") == derive_seed(42, b"a", b"b")
    assert derive_seed(0, b"") == derive_seed(0, b"")


def test_derive_seed_sensitive_to_root():
    """Changing only the root changes the seed."""
    assert derive_seed(1, b"x") != derive_seed(2, b"x")


def test_derive_seed_sensitive_to_parts():
    """Changing any part-byte changes the seed."""
    s_ab = derive_seed(42, b"a", b"b")
    s_ac = derive_seed(42, b"a", b"c")
    s_bb = derive_seed(42, b"b", b"b")
    assert len({s_ab, s_ac, s_bb}) == 3


def test_derive_seed_order_sensitive():
    """Concatenation order matters: derive_seed(r, a, b) ≠ derive_seed(r, b, a)."""
    assert derive_seed(7, b"a", b"b") != derive_seed(7, b"b", b"a")


def test_derive_seed_accepts_str_parts():
    """Strings are auto-encoded as UTF-8 for caller ergonomics."""
    assert derive_seed(1, "hello") == derive_seed(1, b"hello")
    assert derive_seed(1, "över") == derive_seed(1, "över".encode("utf-8"))


def test_derive_seed_returns_unsigned_64bit():
    """Seed always fits in unsigned 64-bit."""
    for inputs in [(0, b""), (123456789, b"x" * 1000), (2**63, b"z")]:
        s = derive_seed(*inputs)
        assert 0 <= s < 2**64


def test_derive_seed_root_out_of_range_raises():
    """Negative roots or values > 2**64-1 should raise."""
    with pytest.raises(OverflowError):
        derive_seed(-1, b"x")
    with pytest.raises(OverflowError):
        derive_seed(2**64, b"x")


# ---------------------------------------------------------------------------
# content_hash


def test_content_hash_deterministic():
    """Same payload → same 32-byte SHA-256."""
    a = content_hash({"k": "v", "n": 1})
    b = content_hash({"n": 1, "k": "v"})  # key-order should not matter
    assert a == b
    assert len(a) == 32


def test_content_hash_sensitive_to_value_change():
    a = content_hash({"k": "v"})
    b = content_hash({"k": "w"})
    assert a != b


def test_content_hash_handles_non_json_types():
    """`default=str` lets us hash datetimes, paths, etc."""
    from datetime import datetime
    from pathlib import Path

    ch_dt = content_hash({"when": datetime(2026, 5, 12, 2, 0, 0)})
    ch_p = content_hash({"path": Path("/tmp/x")})
    assert len(ch_dt) == 32
    assert len(ch_p) == 32


# ---------------------------------------------------------------------------
# DeterminismToken round-trip


def test_token_hex_round_trip():
    """(iii) to_hex / from_hex is identity."""
    tok = DeterminismToken(seed=0xDEADBEEF, content_hash=b"\xab" * 32)
    s = tok.to_hex()
    tok2 = DeterminismToken.from_hex(s)
    assert tok == tok2
    assert tok2.to_hex() == s


def test_token_hex_format_is_stable():
    """Hex format documented: `<16-char-seed>:<64-char-hash>:<version>`."""
    tok = DeterminismToken(seed=1, content_hash=b"\x00" * 32)
    s = tok.to_hex()
    parts = s.split(":")
    assert len(parts) == 3
    assert len(parts[0]) == 16
    assert len(parts[1]) == 64
    assert parts[2] == "v1"


def test_token_validates_seed_range():
    with pytest.raises(ValueError):
        DeterminismToken(seed=-1, content_hash=b"\x00" * 32)
    with pytest.raises(ValueError):
        DeterminismToken(seed=2**64, content_hash=b"\x00" * 32)


def test_token_validates_hash_length():
    with pytest.raises(ValueError):
        DeterminismToken(seed=0, content_hash=b"\x00" * 31)
    with pytest.raises(ValueError):
        DeterminismToken(seed=0, content_hash=b"")


def test_token_from_hex_rejects_malformed():
    with pytest.raises(ValueError):
        DeterminismToken.from_hex("not-a-token")
    with pytest.raises(ValueError):
        DeterminismToken.from_hex("0123456789abcdef:short:v1")
    with pytest.raises(ValueError):
        DeterminismToken.from_hex("not-hex-at-all:" + "ab" * 32 + ":v1")


# ---------------------------------------------------------------------------
# make_token convenience


def test_make_token_idempotent():
    payload = {"robot": "/W/R", "pick": [0, 0, 0]}
    a = make_token(42, payload)
    b = make_token(42, payload)
    assert a == b


def test_make_token_with_extra_parts():
    """Extra parts contribute to seed derivation."""
    payload = {"x": 1}
    t1 = make_token(42, payload)
    t2 = make_token(42, payload, b"extra")
    # Same content hash (from payload), different seed (extra parts)
    assert t1.content_hash == t2.content_hash
    assert t1.seed != t2.seed
