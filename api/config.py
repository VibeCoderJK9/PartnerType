"""Fail-closed, instance-scoped runtime configuration. No implicit dev mode."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def secret(name, required=False):
    path = os.environ.get(name + '_FILE')
    value = Path(path).read_text().strip() if path else os.environ.get(name, '')
    if required and len(value) < 32:
        raise ValueError(name + ' must contain at least 32 characters')
    return value


@dataclass(frozen=True)
class Config:
    environment: str
    instance_id: str
    name: str
    base_url: str
    origins: tuple
    database_url: str
    session_secret: str

    @classmethod
    def load(cls):
        env = os.environ.get('PARTNERTYPE_ENV', '')
        if env not in ('development', 'test', 'staging', 'production'):
            raise ValueError('Set PARTNERTYPE_ENV explicitly')
        instance = os.environ.get('PARTNERTYPE_INSTANCE_ID', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]{2,47}', instance):
            raise ValueError('Invalid PARTNERTYPE_INSTANCE_ID')
        name = os.environ.get('PARTNERTYPE_INSTANCE_NAME', '').strip()
        if not name or len(name) > 200:
            raise ValueError('Set PARTNERTYPE_INSTANCE_NAME')
        from api.http_security import normalize_origin
        base = os.environ.get('PARTNERTYPE_BASE_URL', '').rstrip('/')
        origin = normalize_origin(base)
        if not origin or base != origin:
            raise ValueError('PARTNERTYPE_BASE_URL must be a canonical origin')
        origins = tuple(os.environ.get('PARTNERTYPE_ALLOWED_ORIGINS', base).split(','))
        if any(normalize_origin(x) != x for x in origins) or base not in origins:
            raise ValueError('Invalid allowed origins')
        if env in ('staging', 'production'):
            if any(not x.startswith('https://') for x in origins):
                raise ValueError('HTTPS required')
            if os.environ.get('FLASK_DEBUG', '0') not in ('0', 'false', ''):
                raise ValueError('Debug forbidden')
        dsn = secret('DATABASE_URL')
        parsed = urlsplit(dsn)
        if parsed.scheme not in ('postgresql', 'postgres') or not parsed.hostname or not parsed.username or not parsed.password or not parsed.path.strip('/'):
            raise ValueError('An explicit PostgreSQL DATABASE_URL is required')
        if env in ('staging', 'production') and (len(parsed.password) < 32 or parsed.username != 'partnerhub_app'):
            raise ValueError('Use a restricted app role and strong database password')
        if env == 'test' and not parsed.path[1:].startswith('ph_test_'):
            raise ValueError('Test databases must start with ph_test_')
        return cls(env, instance, name, base, origins, dsn, secret('PARTNERTYPE_SESSION_SECRET', True))
