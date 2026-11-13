"""Windows DPAPI: encrypt a secret so only the same Windows user can decrypt it.

Used for the key that encrypts the Gmail sign-in token, so that key never sits in
plain text on disk. Same mechanism Chrome and Edge use for saved passwords.
"""
import ctypes
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_UI_FORBIDDEN = 0x1


def _call(fn, data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    if not fn(ctypes.byref(src), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _call(ctypes.windll.crypt32.CryptProtectData, data)


def unprotect(data: bytes) -> bytes:
    return _call(ctypes.windll.crypt32.CryptUnprotectData, data)


if __name__ == "__main__":
    secret = b"example-key"
    sealed = protect(secret)
    assert sealed != secret and secret not in sealed
    assert unprotect(sealed) == secret
    try:
        unprotect(sealed[:-4] + b"xxxx")
        raise AssertionError("tampered data must not decrypt")
    except OSError:
        pass
    print("dpapi round-trip ok")
