"""Local operator CLI. Never publishes a service outside localhost.

An instance created with --edge is still bound to localhost only; the public
side is the HTTPS proxy in tools/edge.py.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import subprocess
import sys
from urllib.parse import urlsplit
from deploy.compose import document

ROOT = Path(__file__).resolve().parents[1]
# An instance ID becomes a directory name, a Compose project name and part of a
# host name, so it is held to a narrow alphabet. It can never start with a dot:
# dot-directories beside the instances are the tooling's own scratch space.
INSTANCE_ID = re.compile(r'[a-z][a-z0-9-]{2,47}')
# Names the per-environment stacks and files use beside the instance
# directories (tools/edge.py, mail.json). An instance with one of these names
# would share a directory, and with it a Compose project, with that stack.
RESERVED_IDS = frozenset({'edge', 'mail'})


def check_id(instance_id):
    if not isinstance(instance_id, str) or not INSTANCE_ID.fullmatch(instance_id):
        raise ValueError('Invalid instance ID')
    if instance_id in RESERVED_IDS:
        raise ValueError('Reserved instance ID: ' + instance_id)
    return instance_id


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def write_new(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w') as file:
        file.write(text)


def replace_json(directory, filename, data):
    """Replace an instance file in one step, whatever the previous run left.

    A fixed `.new` name written with O_EXCL meant a single interrupted upgrade
    left a file behind that refused every upgrade afterwards.
    """
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=filename + '.', suffix='.new')
    with os.fdopen(handle, 'w') as file:
        file.write(json.dumps(data, indent=2))
    os.chmod(temporary, 0o600)
    os.replace(temporary, directory / filename)


def compose(directory, *args, **kwargs):
    return run(['docker','compose','-f',str(directory/'compose.json'),*args], **kwargs)


def database(directory):
    compose(directory, 'up','-d','--wait','db')


def model(directory):
    compose(directory,'up','-d','ollama')
    # Model instructions come from the selected immutable app image, not the
    # operator's potentially newer working tree.
    modelfile=compose(directory,'run','--rm','--no-deps','-T','app','python','-c',
        'from pathlib import Path; print(Path("/app/Modelfile").read_text(), end="")',
        capture_output=True,text=True).stdout
    compose(directory,'exec','-T','ollama','sh','-c','cat > /tmp/Modelfile',input=modelfile,text=True)
    # The base model is whatever that image's Modelfile declares; naming it
    # twice here is how a changed FROM ends up pulling the old model.
    base=re.search(r'(?m)^\s*FROM\s+(\S+)\s*$',modelfile)
    if not base:
        raise ValueError('The image Modelfile has no FROM line')
    base=base.group(1)
    try:
        compose(directory,'exec','-T','ollama','ollama','show',base,capture_output=True)
    except subprocess.CalledProcessError:
        compose(directory,'exec','-T','ollama','ollama','pull',base)
    compose(directory,'exec','-T','ollama','ollama','create','partnerhub-agent','-f','/tmp/Modelfile')


MAIL_KEYS = ('host', 'port', 'user', 'from')


def operator_mail(directory):
    """The server's one outgoing mailbox, shared by every instance on it.

    Kept once per environment, beside the instances: instances/<env>/mail.json
    ({"host", "port", "user", "from"}) and instances/<env>/mail_password. A
    producer never types mail server credentials. Without
    the file an instance simply sends no mail, as before.
    """
    source = directory.parent/'mail.json'
    if not source.exists():
        return {}, ''
    mail = json.loads(source.read_text())
    unknown = set(mail) - set(MAIL_KEYS)
    if unknown or not mail.get('host'):
        raise ValueError('mail.json needs "host" and may hold only: ' + ', '.join(MAIL_KEYS))
    password = directory.parent/'mail_password'
    return mail, (password.read_text().strip() if password.exists() else '')


def apply_mail(directory):
    """Refresh an existing instance's mail settings from the operator's files."""
    config = json.loads((directory/'config.json').read_text())
    config['mail'], password = operator_mail(directory)
    secret = directory/'secrets'/'smtp_password'
    secret.chmod(0o600); secret.write_text(password); secret.chmod(0o444)
    for filename, data in [('config.json', config), ('compose.json', document(config, directory))]:
        replace_json(directory, filename, data)


_CADDY_LOG = ('    log default {\n        format filter {\n            wrap json\n'
              '            request delete\n            resp_headers delete\n        }\n    }\n')


# Compression for the files a browser downloads on every visit -- pages,
# stylesheets, scripts -- and nothing else. API answers are left alone on
# purpose: a compressed response that carries a secret next to text an
# attacker can inject leaks the secret through its length (BREACH). Several
# values of one header inside a single matcher are alternatives.
ENCODE_STATIC = ('    encode {\n        zstd\n        gzip\n        match {\n'
                 '            header Content-Type text/html*\n'
                 '            header Content-Type text/css*\n'
                 '            header Content-Type application/javascript*\n'
                 '            header Content-Type text/javascript*\n'
                 '            header Content-Type image/svg+xml*\n'
                 '        }\n    }\n')


def caddyfile(hostname, edge=False, upstream='ollama:8000'):
    """This instance's own proxy.

    Alone, it terminates TLS with Caddy's internal certificate. Behind the edge
    it takes plain HTTP from it and trusts it -- and only the private ranges the
    edge reaches it from -- to report the client address and the https scheme.
    """
    if edge:
        return ('{\n    auto_https off\n    servers {\n        trusted_proxies static private_ranges\n    }\n'
                + _CADDY_LOG + '}\n'
                'http://' + hostname + ':80 {\n' + ENCODE_STATIC + '    reverse_proxy ' + upstream + '\n}\n')
    return ('{\n    auto_https disable_redirects\n' + _CADDY_LOG + '}\n'
            + hostname + ':443 {\n    tls internal\n' + ENCODE_STATIC + '    reverse_proxy ' + upstream + '\n}\n')


def write_caddyfile(directory, config):
    """(Re)write this instance's Caddyfile, readable by everyone.

    It holds no secret. The proxy container runs without CAP_DAC_OVERRIDE, so
    with a non-root operator a 0600 file owned by the operator is unreadable
    inside it and the proxy never starts.
    """
    path = directory/'Caddyfile'
    text = caddyfile(urlsplit(config['base_url']).hostname, config.get('edge', False))
    if path.exists():
        path.chmod(0o644)
        path.write_text(text)
    else:
        write_new(path, text, 0o644)
    path.chmod(0o644)


# Keys the operator's tooling changes after creation (knowledge-quota,
# destroy-data). A re-run of the same `create` must not read them as a
# different configuration.
LIFECYCLE_KEYS = ('project_name', 'db_name', 'mail', 'destroyed', 'knowledge_quota_mb')
# The space the knowledge base may use, in MB: the same range api/knowledge.py
# accepts, so the app never receives a value it would refuse.
KNOWLEDGE_QUOTA_RANGE = (1, 1024 * 1024)


def instance_configs(environment_root):
    """(directory, config) of every instance in one environment.

    Dot-directories are skipped: they are create's staging area and the
    restore drill's temporary copies, never a producer's instance.
    """
    for config_file in sorted(environment_root.glob('*/config.json')):
        if not config_file.parent.name.startswith('.'):
            yield config_file.parent, json.loads(config_file.read_text())


def addresses_in_use(environment_root):
    """Host names and localhost ports already held in this environment.

    A destroyed instance keeps its address: `create` may bring it back with
    the same configuration.
    """
    hosts, ports = set(), set()
    for _, config in instance_configs(environment_root):
        hosts.add(urlsplit(config['base_url']).hostname)
        ports.add(int(config['port']))
    return hosts, ports


def startable(config):
    """Whether a command may bring this instance's containers up.

    A destroyed instance has no data left to serve.
    """
    return not config.get('destroyed')


# The only commands that still make sense once the data is gone: neither can
# start a container, and both may be repeated safely.
ALLOWED_WHEN_DESTROYED = ('stop', 'destroy-data')


def update_config(directory, **changes):
    """Rewrite config.json and compose.json together; None removes a key."""
    config = json.loads((directory/'config.json').read_text())
    for key, value in changes.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    for filename, data in [('config.json', config), ('compose.json', document(config, directory))]:
        replace_json(directory, filename, data)
    return config


def create(args, directory):
    check_id(args.id)
    if not re.fullmatch(r'(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})', args.image):
        raise ValueError('Use an immutable Docker image ID or digest')
    base = urlsplit(args.base_url)
    if base.scheme != 'https' or not base.hostname or base.username or base.password or base.path or base.query or base.fragment:
        raise ValueError('Use an HTTPS origin without a trailing slash')
    edge = getattr(args, 'edge', False)
    if edge:
        # The public address is the edge's (443); --port is only where this
        # instance listens on localhost for it.
        if base.port is not None:
            raise ValueError('Behind the edge the URL has no port')
    elif base.port != args.port and not (base.port is None and args.port == 443):
        raise ValueError('URL port must match --port')
    if not re.fullmatch(r'[a-z0-9.-]+', base.hostname):
        raise ValueError('Invalid hostname')
    config = {'id':args.id,'environment':args.env,'name':args.name,'base_url':args.base_url,
              'image':args.image,'port':args.port}
    # Recorded only when they differ from the default, so instances created
    # before these options compare equal on a re-run of the same command.
    if edge: config['edge'] = True
    if getattr(args, 'no_ai', False): config['ai'] = False
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Re-running create after destroy-data initialises an empty set again; the
    # instance stops counting as destroyed once that has worked, or the edge
    # would keep ignoring it.
    revived = False
    if directory.exists():
        saved = json.loads((directory/'config.json').read_text())
        if {k:v for k,v in saved.items() if k not in LIFECYCLE_KEYS} != config:
            raise ValueError('Instance exists with different configuration; nothing overwritten')
        revived = bool(saved.get('destroyed'))
    else:
        # Checked before anything is written: a second instance on a taken
        # address or port would fail to start, stay on disk, and block every
        # later edge update until somebody found and removed it by hand.
        hosts, ports = addresses_in_use(directory.parent)
        if base.hostname in hosts:
            raise ValueError('Another instance already uses this host name; nothing created')
        if args.port in ports:
            raise ValueError('Another instance already uses this port; nothing created')
        staging=Path(tempfile.mkdtemp(prefix='.prepare-',dir=directory.parent))
        config['project_name']='ph-'+args.env+'-'+args.id+'-'+secrets.token_hex(4)
        config['db_name']='ph_test_instance' if args.env=='test' else 'partnerhub'
        config['mail'], mail_password = operator_mail(directory)
        (staging/'secrets').mkdir(mode=0o700)
        owner, app = secrets.token_hex(32), secrets.token_hex(32)
        values = {'db_owner_password': owner, 'db_password': app,
            'database_url': f'postgresql://partnerhub_app:{app}@db/{config["db_name"]}',
            'migration_database_url': f'postgresql://partnerhub_owner:{owner}@db/{config["db_name"]}',
            'session_secret': secrets.token_hex(32), 'smtp_password': mail_password}
        for key,value in values.items():
            # Parent directory is 0700. Readable within the non-root container.
            write_new(staging/'secrets'/key, value, 0o444)
        write_new(staging/'config.json',json.dumps(config,indent=2))
        write_new(staging/'compose.json',json.dumps(document(config,directory),indent=2))
        write_caddyfile(staging, config)
        staging.rename(directory)
    if args.prepare_only:
        if revived: update_config(directory, destroyed=None)
        print('Configuration ready:', directory)
        return
    database(directory)
    compose(directory,'run','--rm','migrate')
    compose(directory,'up','-d','ollama')
    command = ['run','--rm','--no-deps','-T','app','python','-m','tools.bootstrap']
    result = compose(directory,*command,input=json.dumps({'email':args.email,'name':args.name}),text=True,capture_output=True)
    invitation = json.loads(result.stdout)
    if invitation['created']:
        write_new(directory/'owner-invitation.json',json.dumps(invitation,indent=2))
        print('One-time owner invitation:', directory/'owner-invitation.json')
    compose(directory,'up','-d','--wait','--wait-timeout','120','app','proxy', *(['mail'] if args.env!='production' else []))
    if revived: update_config(directory, destroyed=None)
    if config.get('ai', True) is False:
        print('Instance ready without the assistant:',config['base_url'])
        return
    print('CRM ready. Preparing local AI model.')
    if not getattr(args,'skip_model',False):model(directory)
    print('Instance ready:',config['base_url'])


def database_name(directory):
    return json.loads((directory/'config.json').read_text()).get('db_name','partnerhub')


def backup(directory, output, recipient):
    """Encrypted dump, under its final name only once it is known to be complete.

    When pg_dump dies halfway, age still closes a correctly authenticated
    stream of the truncated dump. Writing to a temporary name and renaming
    only after both processes succeeded means a `*.age` file is never a
    partial backup that a shipping job would mistake for a good one.
    """
    if not recipient:
        raise ValueError('An age recipient is required; plaintext backups are forbidden')
    output = Path(output)
    if output.exists():
        raise ValueError('Backup file exists; nothing overwritten')
    handle, partial = tempfile.mkstemp(dir=output.parent, prefix=output.name+'.', suffix='.partial')
    try:
        with os.fdopen(handle, 'wb') as file:
            dump = subprocess.Popen(['docker','compose','-f',str(directory/'compose.json'),'exec','-T','db',
                'pg_dump','-U','partnerhub_owner','-d',database_name(directory),'-Fc','--no-owner','--no-acl'],stdout=subprocess.PIPE)
            try:
                result = subprocess.run(['age','-r',recipient],stdin=dump.stdout,stdout=file)
                dump.stdout.close()
                if dump.wait() or result.returncode:
                    raise RuntimeError('Backup failed; no backup file was written')
            finally:
                if dump.poll() is None:
                    dump.kill(); dump.wait()
            file.flush(); os.fsync(file.fileno())
        # link() never replaces an existing file, unlike rename().
        os.link(partial, output)
    finally:
        Path(partial).unlink(missing_ok=True)
    print('Encrypted backup:',output)


def adopt_session_secret(directory, source):
    """Give a restore target the original instance's session secret.

    Authenticator-app enrolments are encrypted with a key derived from the
    session secret, so a restore into an instance with its own new secret
    leaves every enrolled user's codes unreadable.
    """
    value = Path(source).read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{32,}', value):
        raise ValueError('--session-secret-from must be the original instance\'s secrets/session_secret file')
    secret = directory/'secrets'/'session_secret'
    secret.chmod(0o600); secret.write_text(value); secret.chmod(0o444)


def restore(directory, source, identity, session_secret_from=None):
    database(directory)
    result = compose(directory,'exec','-T','db','psql','-U','partnerhub_owner','-d',database_name(directory),'-Atc',
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'",capture_output=True,text=True)
    if result.stdout.strip() != '0':
        # Refuse before touching services: a running instance stays running.
        raise ValueError('Restore target must be a new, empty instance created with --prepare-only')
    if session_secret_from:
        adopt_session_secret(directory, session_secret_from)
    compose(directory,'stop','app','proxy')
    decrypt = subprocess.Popen(['age','-d','-i',identity,source],stdout=subprocess.PIPE)
    try:
        compose(directory,'exec','-T','db','pg_restore','-U','partnerhub_owner','-d',database_name(directory),
                '--no-owner','--no-acl','--exit-on-error','--single-transaction',stdin=decrypt.stdout)
        decrypt.stdout.close()
        if decrypt.wait(): raise RuntimeError('Decryption failed')
    finally:
        if decrypt.poll() is None: decrypt.kill()
    compose(directory,'exec','-T','db','psql','-U','partnerhub_owner','-d',database_name(directory),'-v','ON_ERROR_STOP=1','-c',
        'TRUNCATE sessions, partner_invitations, agent_pending_actions;')
    compose(directory,'run','--rm','migrate')
    print('Restored CRM; all sessions and invitation/action tokens invalidated. Start explicitly.')
    if not session_secret_from:
        enrolled = compose(directory,'exec','-T','db','psql','-U','partnerhub_owner','-d',database_name(directory),'-Atc',
            'SELECT u.email FROM user_mfa m JOIN users u ON u.id = m.user_id WHERE m.confirmed_at IS NOT NULL ORDER BY u.email',
            capture_output=True,text=True).stdout.split()
        if enrolled:
            print('WARNING: this instance has its own session secret, so the authenticator app of these '
                  'users no longer works (backup codes still do). Before start, copy the original '
                  "instance's secrets/session_secret over this one's (restore --session-secret-from does "
                  'that), or run mfa-reset --email for each:')
            for email in enrolled:
                print('  '+email)


def parse_periods(text):
    """`notifications=180,audit_log=730` as {category: days}, or a ValueError.

    Parsed here and handed to the container as data, never pasted into the
    Python source it runs.
    """
    periods = {}
    for part in filter(None, (text or '').split(',')):
        key, _, value = part.strip().partition('=')
        if not re.fullmatch(r'[a-z_]{1,40}', key) or not re.fullmatch(r'[0-9]{1,6}', value) or int(value) < 1:
            raise ValueError('--periods expects category=days pairs, e.g. notifications=180,audit_log=730')
        periods[key] = int(value)
    return periods


def retention_script(periods, apply):
    return ('import json;from api import retention;periods=%r;apply=%r;'
            'print(json.dumps(retention.survey() if not periods else '
            '(retention.apply(periods,confirm=True) if apply else retention.preview(periods)),'
            'indent=2,ensure_ascii=False))') % (periods, bool(apply))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'instances')
    parser.add_argument('--env',required=True,choices=['development','test','staging','production'])
    parser.add_argument('--id',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    new=sub.add_parser('create')
    new.add_argument('--name',required=True);new.add_argument('--email',required=True)
    new.add_argument('--base-url',required=True);new.add_argument('--port',type=int,required=True)
    new.add_argument('--image',required=True);new.add_argument('--prepare-only',action='store_true')
    new.add_argument('--skip-model',action='store_true',help='Start CRM first; run model explicitly later')
    new.add_argument('--edge',action='store_true',help='Behind the public HTTPS proxy (tools/edge.py)')
    new.add_argument('--no-ai',action='store_true',help='No assistant, no model')
    quota=sub.add_parser('knowledge-quota',help='Space for the knowledge base, in MB')
    quota_kind=quota.add_mutually_exclusive_group(required=True)
    quota_kind.add_argument('--mb',type=int);quota_kind.add_argument('--default',action='store_true')
    for command in ('migrate','start','stop','model','maintenance','mail'):sub.add_parser(command)
    mail_test=sub.add_parser('mail-test');mail_test.add_argument('--to',required=True)
    retention=sub.add_parser('retention')
    retention.add_argument('--periods',help='e.g. notifications=180,audit_log=730; without it only a report')
    retention.add_argument('--apply',action='store_true',help='carry out the preview; without it nothing is deleted')
    upgrade=sub.add_parser('upgrade');upgrade.add_argument('--image',required=True)
    b=sub.add_parser('backup');b.add_argument('output');b.add_argument('--recipient',required=True)
    r=sub.add_parser('restore');r.add_argument('source');r.add_argument('--identity',required=True)
    r.add_argument('--session-secret-from',type=Path,
                   help="The original instance's secrets/session_secret; keeps its users' authenticator apps working")
    load=sub.add_parser('load-test');load.add_argument('--report',required=True,type=Path)
    d=sub.add_parser('destroy-data');d.add_argument('--confirm-id',required=True)
    # The way back in for somebody who lost the phone AND the printed codes.
    # Deliberately not reachable from a browser: the producer has nobody above
    # him, so the bar for reopening his account is access to the server itself.
    mfa=sub.add_parser('mfa-reset');mfa.add_argument('--email',required=True)
    # A fresh sign-in link for an owner who never set a password (lost or
    # expired first e-mail). Prints {"created": bool, "invite_url"?} only; it
    # goes to the address the owner confirmed at sign-up, never to a browser.
    sub.add_parser('owner-invite',help='New one-time link for an owner who never signed in')
    args=parser.parse_args()
    try:check_id(args.id)
    except ValueError as error:parser.error(str(error))
    directory=args.root.resolve()/args.env/args.id
    if args.command=='create':return create(args,directory)
    if not (directory/'config.json').exists():parser.error('Unknown instance')
    current=json.loads((directory/'config.json').read_text())
    if current.get('destroyed') and args.command not in ALLOWED_WHEN_DESTROYED:
        # Starting anything here would create an empty database volume and run
        # an instance without an owner -- what destroy-data was run to end.
        raise ValueError('This instance\'s data was destroyed; run create again to initialise it')
    if args.command=='migrate':
        database(directory);compose(directory,'run','--rm','migrate')
    elif args.command=='start':
        compose(directory,'up','-d')
    elif args.command=='stop':compose(directory,'down') # intentionally no volumes flag
    elif args.command=='retention':
        # Report and preview only, unless --apply is spelled out: deciding how
        # long a producer keeps a record is his call, not a default.
        try:periods=parse_periods(args.periods)
        except ValueError as error:parser.error(str(error))
        compose(directory,'run','--rm','--no-deps','app','python','-c',retention_script(periods,args.apply))
    elif args.command=='owner-invite':
        result=compose(directory,'run','--rm','--no-deps','-T','app','python','-m','tools.bootstrap',
                       input=json.dumps({'reinvite':True}),text=True,capture_output=True)
        print(json.dumps(json.loads(result.stdout.strip().splitlines()[-1])))
    elif args.command=='mfa-reset':
        script=('import json;from api.db import get_conn;from api.postgres import system_context;'
                'email=%r.strip().lower();conn=get_conn()\n'
                'try:\n'
                '    with system_context("operator: mfa-reset"):\n'
                '        row=conn.execute("SELECT id,name FROM users WHERE lower(email)=?",(email,)).fetchone()\n'
                '        if not row: print(json.dumps({"found":False}));raise SystemExit(0)\n'
                '        conn.begin()\n'
                '        conn.execute("DELETE FROM user_mfa WHERE user_id=?",(row["id"],))\n'
                '        conn.execute("DELETE FROM user_mfa_backup_codes WHERE user_id=?",(row["id"],))\n'
                '        conn.execute("DELETE FROM mfa_challenges WHERE user_id=?",(row["id"],))\n'
                '        conn.commit()\n'
                '        from api import audit\n'
                # Recorded with no actor, like a refused login: nobody was
                # signed in, and an account reopened from the server shell is
                # exactly the entry an audit is read for.
                '        audit.log(None,"user.mfa_reset","user",str(row["id"]),"operator command")\n'
                '        print(json.dumps({"found":True,"name":row["name"]}))\n'
                'finally:\n'
                '    conn.close()')%args.email
        compose(directory,'run','--rm','--no-deps','app','python','-c',script)
        print('Second factor removed. Sign in with the password alone and set it up again.')
    elif args.command=='maintenance':
        compose(directory,'run','--rm','--no-deps','app','python','-c','from api.db import run_maintenance; print(run_maintenance())')
    elif args.command=='knowledge-quota':
        low,high=KNOWLEDGE_QUOTA_RANGE
        if args.mb is not None and not low<=args.mb<=high:
            raise ValueError(f'--mb must be between {low} and {high}')
        config=update_config(directory, knowledge_quota_mb=None if args.default else args.mb)
        if startable(config):
            compose(directory,'up','-d','--force-recreate','--no-deps','app')
    elif args.command=='model':
        mode=json.loads((directory/'config.json').read_text()).get('ai',True)
        if mode is False:
            raise ValueError('This instance was created without the assistant')
        model(directory)
    elif args.command=='mail':
        # After editing instances/<env>/mail.json or mail_password.
        apply_mail(directory)
        if startable(current):compose(directory,'up','-d','--force-recreate','app')
    elif args.command=='mail-test':
        script='import json;from api.email_service import send_test;print(json.dumps(send_test(%r)))'%args.to
        compose(directory,'run','--rm','--no-deps','app','python','-c',script)
    elif args.command=='upgrade':
        if not re.fullmatch(r'(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})',args.image):
            raise ValueError('Immutable image required')
        config=json.loads((directory/'config.json').read_text())
        compose(directory,'stop','app')
        config['image']=args.image
        config['mail'],_=operator_mail(directory)
        for filename,data in [('config.json',config),('compose.json',document(config,directory))]:
            replace_json(directory,filename,data)
        # The proxy's configuration is generated too; an upgrade is where an
        # instance picks up a change to it.
        write_caddyfile(directory, config)
        database(directory);compose(directory,'run','--rm','migrate')
        compose(directory,'up','-d')
        compose(directory,'restart','proxy')
    elif args.command=='load-test':
        if args.report.exists():raise ValueError('Report exists')
        import threading
        readings=[];done=threading.Event()
        ids=compose(directory,'ps','-q',capture_output=True,text=True).stdout.split()
        def sample():
            while not done.is_set():
                result=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',*ids],capture_output=True,text=True)
                if result.returncode==0:
                    readings.extend(json.loads(line) for line in result.stdout.splitlines() if line)
                done.wait(1)
        sampler=threading.Thread(target=sample,daemon=True);sampler.start()
        try:
            result=compose(directory,'run','--rm','--no-deps','-T','app','python','-m','tools.load_test',capture_output=True,text=True)
            report=json.loads(result.stdout)
            report['container_resources']=readings
            write_new(args.report,json.dumps(report,indent=2))
        finally:
            done.set();sampler.join(timeout=5)
        print('Synthetic load report:',args.report)
    elif args.command=='backup':backup(directory,args.output,args.recipient)
    elif args.command=='restore':restore(directory,args.source,args.identity,args.session_secret_from)
    elif args.command=='destroy-data':
        if args.confirm_id != args.id:parser.error('Explicit matching --confirm-id required')
        compose(directory,'down','--volumes')
        (directory/'owner-invitation.json').unlink(missing_ok=True)
        # Kept as a record that this address once existed; the edge skips it.
        update_config(directory, destroyed=True)


if __name__=='__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        # Never render CalledProcessError output, DSNs or secrets.
        print(str(error) if isinstance(error,(ValueError,RuntimeError)) else 'Operator command failed: '+type(error).__name__,file=sys.stderr)
        sys.exit(1)
