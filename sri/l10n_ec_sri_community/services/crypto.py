# -*- coding: utf-8 -*-
"""Cifrado simétrico del certificado de firma (.p12) y su contraseña.

El material se guarda CIFRADO en la BD. La llave maestra vive en la variable
de entorno EC_SRI_MASTER_KEY (nunca en la BD): de una passphrase + un salt por
compañía se deriva una clave Fernet (PBKDF2-HMAC-SHA256). Un dump de la BD sin
esa variable es inútil."""

import base64
import os
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MASTER_ENV = 'EC_SRI_MASTER_KEY'
_ITERATIONS = 390000


def new_salt():
    """Salt aleatorio (no secreto) codificado en base64 para guardar en BD."""
    return base64.b64encode(os.urandom(16)).decode('ascii')


MASTER_FILE_ENV = 'EC_SRI_MASTER_KEY_FILE'


def _master_key():
    """La llave en EC_SRI_MASTER_KEY o, si no está, en el archivo indicado por
    EC_SRI_MASTER_KEY_FILE (convención de secrets de Docker/Compose), para no
    exponerla en el entorno del proceso."""
    passphrase = os.environ.get(MASTER_ENV)
    if not passphrase and os.environ.get(MASTER_FILE_ENV):
        try:
            with open(os.environ[MASTER_FILE_ENV], encoding='utf-8') as fh:
                passphrase = fh.read().strip()
        except OSError as exc:
            raise ValueError('No se pudo leer el archivo de EC_SRI_MASTER_KEY_FILE.') from exc
    return passphrase


def has_master_key():
    return bool(_master_key())


def _fernet(salt_b64):
    passphrase = _master_key()
    if not passphrase:
        raise ValueError('Falta la variable de entorno EC_SRI_MASTER_KEY (o EC_SRI_MASTER_KEY_FILE) en el servidor.')
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=base64.b64decode(salt_b64), iterations=_ITERATIONS)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode('utf-8'))))


def encrypt(data, salt_b64):
    """data: bytes -> token str (ascii)."""
    return _fernet(salt_b64).encrypt(data).decode('ascii')


def decrypt(token, salt_b64):
    """token: str -> bytes. Lanza ValueError si la llave es incorrecta."""
    try:
        return _fernet(salt_b64).decrypt(token.encode('ascii'))
    except InvalidToken as exc:
        raise ValueError('No se pudo descifrar el certificado: la llave EC_SRI_MASTER_KEY '
                         'no coincide con la usada al guardarlo.') from exc
