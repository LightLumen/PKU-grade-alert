"""Windows-user-scoped encrypted storage for optional login credentials."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


MAGIC = b"PKU-GRADE-ALERT-DPAPI\x00"


class SecretStorageError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _require_windows() -> None:
    if os.name != "nt":
        raise SecretStorageError("自动登录凭据加密目前仅支持 Windows")


def _blob_from_bytes(value: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(value)
    blob = DATA_BLOB(
        len(value),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _crypt_protect(value: bytes) -> bytes:
    _require_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, source_buffer = _blob_from_bytes(value)
    result = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "PKU Grade Alert credentials",
        None,
        None,
        None,
        0x01,
        ctypes.byref(result),
    ):
        raise SecretStorageError(f"Windows DPAPI 加密失败：{ctypes.get_last_error()}")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _crypt_unprotect(value: bytes) -> bytes:
    _require_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, source_buffer = _blob_from_bytes(value)
    result = DATA_BLOB()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        None,
        None,
        None,
        0x01,
        ctypes.byref(result),
    ):
        raise SecretStorageError(
            f"Windows DPAPI 解密失败：{ctypes.get_last_error()}。请重新保存登录信息"
        )
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(result.pbData)


def save_credentials(path: Path, username: str, password: str) -> None:
    username = username.strip()
    if not username or not password:
        raise SecretStorageError("账号和密码不能为空")
    payload = json.dumps(
        {"username": username, "password": password},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    protected = MAGIC + _crypt_protect(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(protected)
    temporary.replace(path)


def load_credentials(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise SecretStorageError("登录信息文件格式不正确，请清除后重新保存")
    try:
        payload = json.loads(_crypt_unprotect(raw[len(MAGIC) :]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SecretStorageError("登录信息无法解析，请清除后重新保存") from error
    if not isinstance(payload, dict):
        raise SecretStorageError("登录信息格式不正确")
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        raise SecretStorageError("保存的账号或密码为空")
    return {"username": username, "password": password}


def delete_credentials(path: Path) -> None:
    if path.exists():
        path.unlink()
