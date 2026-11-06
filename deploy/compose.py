"""Compose document generator: one independent project per environment/producer."""
from pathlib import Path


# Docker's default json-file driver keeps every line forever. One chatty
# instance would fill the host disk for all the others.
LOGGING = {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '5'}}

# Ceilings, not reservations: they stop one service from starving the rest of
# the host. The AI service gets the bulk because a generation is
# the only thing here that can saturate a machine; tune per host size.
LIMITS = {
    'app':    {'mem_limit': '768m', 'cpus': 2.0,  'pids_limit': 512},
    'db':     {'mem_limit': '1g',   'cpus': 2.0,  'pids_limit': 512},
    'proxy':  {'mem_limit': '256m', 'cpus': 1.0,  'pids_limit': 256},
    'mail':   {'mem_limit': '256m', 'cpus': 0.5,  'pids_limit': 256},
    'ollama': {'mem_limit': '10g',  'cpus': 6.0,  'pids_limit': 1024},
}


# An instance without the assistant keeps the ollama container
# only as the app's network namespace, so it gets a ceiling no model fits in.
LIMITS_WITHOUT_AI = {'mem_limit': '256m', 'cpus': 0.5}


def literal(value):
    """The same document with every `$` doubled, so Compose takes it as written.

    Compose substitutes `$VAR` and `${VAR}` from the operator's shell in every
    string of the file. Values here come from outside (a company name, mail
    settings); without the escape a name could pull an
    operator's environment variable into the instance, be cut short, or make
    every later compose call fail on a malformed `${`.
    """
    if isinstance(value, str):
        return value.replace('$', '$$')
    if isinstance(value, dict):
        return {key: literal(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [literal(item) for item in value]
    return value


# Instance config `api` key -> the app's environment variable.
GATEWAY_ENV = {
    'rate_per_minute': 'PARTNERTYPE_API_RATE_PER_MINUTE',
    'rate_per_day': 'PARTNERTYPE_API_RATE_PER_DAY',
    'register_per_hour': 'PARTNERTYPE_OAUTH_REGISTER_PER_HOUR',
    'mcp_extra_origins': 'PARTNERTYPE_MCP_EXTRA_ORIGINS',
    'oauth_redirect_hosts': 'PARTNERTYPE_OAUTH_REDIRECT_HOSTS',
}


def gateway_environment(config):
    """PARTNERTYPE_* gateway variables for the keys `config['api']` actually sets.

    A list of origins is joined with commas, the form the app reads. Values are
    passed as text; the bounds live in the app, which refuses a value outside
    its range (the gateway fails closed) rather than guessing.
    """
    api = config.get('api') or {}
    environment = {}
    for key, variable in GATEWAY_ENV.items():
        value = api.get(key)
        if value is None or value == '' or value == []:
            continue
        if isinstance(value, (list, tuple)):
            value = ','.join(str(item) for item in value)
        environment[variable] = str(value)
    return environment


def document(config, directory):
    root = Path(__file__).resolve().parents[1]
    directory = Path(directory).resolve()
    environment = config['environment']
    app = {
        'image': config['image'], 'restart': 'unless-stopped',
        'network_mode': 'service:ollama', 'read_only': True,
        'tmpfs': ['/tmp:rw,noexec,nosuid,size=64m'],
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'environment': {
            'PARTNERTYPE_ENV': environment,
            'PARTNERTYPE_INSTANCE_ID': config['id'],
            'PARTNERTYPE_INSTANCE_NAME': config['name'],
            'PARTNERTYPE_BASE_URL': config['base_url'],
            'PARTNERTYPE_ALLOWED_ORIGINS': config['base_url'],
            'DATABASE_URL_FILE': '/run/secrets/database_url',
            'PARTNERTYPE_SESSION_SECRET_FILE': '/run/secrets/session_secret',
            'PARTNERTYPE_SMTP_PASSWORD_FILE': '/run/secrets/smtp_password',
            'OLLAMA_URL': 'http://127.0.0.1:11434', 'OLLAMA_MODEL': 'partnerhub-agent',
            # The operator's one mailbox for every producer (instances/<env>/mail.json).
            **{'PARTNERTYPE_SMTP_'+key.upper(): str(value) for key, value in config.get('mail', {}).items()},
            # Behind the edge proxy there are two hops, edge then this
            # instance's Caddy; the client is the one the edge reported.
            'PARTNERTYPE_PROXY_HOPS': '2' if config.get('edge') else '1',
            'PARTNERTYPE_AI': 'on' if config.get('ai', True) else 'off',
            # Space for the knowledge base, set by the operator (api/knowledge.py).
            **({'PARTNERTYPE_KNOWLEDGE_QUOTA_MB': str(config['knowledge_quota_mb'])}
               if config.get('knowledge_quota_mb') else {}),
            # The operator's ceilings for the API gateway, only when
            # the instance config sets them; otherwise the app's defaults apply
            # (api/gateway/settings.py: operator_caps, mcp_allowed_origins).
            **gateway_environment(config)},
        'secrets': ['database_url', 'session_secret', 'smtp_password'],
        'depends_on': {'db': {'condition': 'service_healthy'}, 'ollama': {'condition': 'service_started'}},
        'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5)"], 'interval': '10s', 'timeout': '6s', 'retries': 6},
    }
    services = {
        # No cap_drop here: the postgres entrypoint starts as root and steps
        # down with gosu, which needs CAP_SETUID/CAP_SETGID to do it.
        'db': {'image': 'postgres:17.6@sha256:00bc86618629af00d2937fdc5a5d63db3ff8450acf52f0636ec813c7f4902929', 'restart': 'unless-stopped',
            'security_opt': ['no-new-privileges:true'],
            'command': ['postgres','-c','log_error_verbosity=terse','-c','log_statement=none','-c','log_min_error_statement=panic'],
            'environment': {'POSTGRES_DB': config.get('db_name','partnerhub'), 'POSTGRES_USER': 'partnerhub_owner', 'POSTGRES_PASSWORD_FILE': '/run/secrets/db_owner_password'},
            'secrets': ['db_owner_password', 'db_password'], 'networks': ['private'],
            'volumes': ['postgres:/var/lib/postgresql/data', str(root/'deploy/init-db.sh')+':/docker-entrypoint-initdb.d/01-app.sh:ro'],
            'healthcheck': {'test': ['CMD', 'pg_isready', '-U', 'partnerhub_owner', '-d', config.get('db_name','partnerhub')], 'interval': '3s', 'timeout': '3s', 'retries': 30}},
        'ollama': {'image': 'ollama/ollama:0.11.10', 'restart': 'unless-stopped',
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            # Keeping the model resident costs its memory permanently and saves
            # the ~105s reload that every question after a pause would pay for.
            'environment': {'OLLAMA_HOST': '127.0.0.1:11434', 'OLLAMA_NUM_PARALLEL': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_KEEP_ALIVE': '-1'},
            'networks': ['private', 'egress'], 'volumes': ['ollama:/root/.ollama'], 'mem_limit': '10g'},
        'app': app,
        'migrate': {'image': config['image'], 'profiles': ['operator'],
            'networks': ['private'], 'command': ['python', '-m', 'tools.migrate'],
            'environment': {'MIGRATION_DATABASE_URL_FILE': '/run/secrets/migration_database_url'},
            'secrets': ['migration_database_url'], 'depends_on': {'db': {'condition': 'service_healthy'}}},
        # Caddy binds 443 inside the container, so it keeps exactly the one
        # capability that requires, and nothing else.
        'proxy': {'image': 'caddy:2.10.2', 'restart': 'unless-stopped',
            'cap_drop': ['ALL'], 'cap_add': ['NET_BIND_SERVICE'],
            'security_opt': ['no-new-privileges:true'],
            # With the edge, TLS ends there and this Caddy speaks plain HTTP on
            # localhost only; without it, it serves its own internal certificate.
            'networks': ['private', 'egress'],
            'ports': [f"127.0.0.1:{config['port']}:{80 if config.get('edge') else 443}"],
            'volumes': [str(directory/'Caddyfile')+':/etc/caddy/Caddyfile:ro', 'caddy:/data'],
            'depends_on': {'app': {'condition': 'service_started'}}},
    }
    if environment != 'production':
        services['mail'] = {'image': 'axllent/mailpit:v1.27.8', 'networks': ['private'],
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'restart': 'unless-stopped'}
    for name, service in services.items():
        service['logging'] = LOGGING
        service.update(LIMITS.get(name, {}))
    if not config.get('ai', True):
        # The container stays: the app lives in its network namespace.
        # Without a model it idles, and the ceiling keeps one from being loaded.
        services['ollama'].update(LIMITS_WITHOUT_AI)
    secrets = {name: {'file': str(directory/'secrets'/name)} for name in
               ('database_url','session_secret','smtp_password','migration_database_url','db_owner_password','db_password')}
    networks = {'private': {'internal': True}, 'egress': {}}
    return literal({'name': config.get('project_name','ph-'+environment+'-'+config['id']), 'services': services,
            'networks': networks,
            'volumes': {x: {} for x in ('postgres','caddy','ollama')}, 'secrets': secrets})
