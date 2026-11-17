"""Local encrypted AI credential vault. Values are scoped to the Windows user."""
import ctypes
import sqlite3
import uuid
from ctypes import wintypes
from pathlib import Path
from contextlib import closing

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

def dpapi(value: bytes, decrypt: bool = False) -> bytes:
    if not hasattr(ctypes, 'windll'):
        raise RuntimeError('Windows DPAPI required')
    buf = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('DPAPI operation failed')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)

def store(path: Path, secret: str) -> str:
    ciphertext = dpapi(secret.encode('utf-8'))
    ref = uuid.uuid4().hex
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS credentials (id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)')
        db.execute('INSERT INTO credentials VALUES (?,?)', (ref, ciphertext))
    return 'vault:' + ref

def load(path: Path, reference: str) -> str:
    with closing(sqlite3.connect(path)) as db, db:
        row = db.execute('SELECT ciphertext FROM credentials WHERE id=?', (reference[6:],)).fetchone()
    if row is None:
        raise RuntimeError('Credential reference missing')
    return dpapi(row[0], decrypt=True).decode('utf-8')
