"""Offline checks only: no database engine is substituted for PostgreSQL."""
import argparse
import json
from pathlib import Path
import pytest
from api.config import Config
from tools.instance import create


def args(**overrides):
    values=dict(id='producer-a',env='staging',name='Synthetic A',email='owner@example.test',
        base_url='https://a.localhost:8443',port=8443,image='sha256:'+'a'*64,prepare_only=True)
    values.update(overrides)
    return argparse.Namespace(**values)


def test_provisioning_is_idempotent_and_secrets_are_unique(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b'
    create(args(),a)
    before={p.name:p.read_bytes() for p in (a/'secrets').iterdir()}
    create(args(),a)
    assert before=={p.name:p.read_bytes() for p in (a/'secrets').iterdir()}
    create(args(id='producer-b',base_url='https://b.localhost:9443',port=9443),b)
    for name in ('db_password','db_owner_password','session_secret'):
        assert (a/'secrets'/name).read_bytes()!=(b/'secrets'/name).read_bytes()
    with pytest.raises(ValueError):create(args(name='changed'),a)


def test_network_and_secret_boundaries(tmp_path):
    create(args(),tmp_path/'a')
    doc=json.loads((tmp_path/'a/compose.json').read_text())
    services=doc['services']
    assert 'ports' not in services['db']
    assert 'ports' not in services['ollama']
    assert services['ollama']['environment']['OLLAMA_HOST']=='127.0.0.1:11434'
    assert services['app']['network_mode']=='service:ollama'
    assert 'volumes' not in services['app']
    assert 'migration_database_url' not in services['app']['secrets']
    assert 'db_owner_password' not in services['app']['secrets']
    assert services['proxy']['ports']==['127.0.0.1:8443:443']
    assert 'mail' in services


def test_production_has_no_mail_capture_and_requires_immutable_image(tmp_path):
    with pytest.raises(ValueError):create(args(image='partnertype:latest'),tmp_path/'bad')
    create(args(env='production'),tmp_path/'prod')
    assert 'mail' not in json.loads((tmp_path/'prod/compose.json').read_text())['services']


@pytest.fixture
def production(monkeypatch):
    values={'PARTNERTYPE_ENV':'production','PARTNERTYPE_INSTANCE_ID':'producer-a',
        'PARTNERTYPE_INSTANCE_NAME':'A','PARTNERTYPE_BASE_URL':'https://a.localhost:8443',
        'DATABASE_URL':'postgresql://partnerhub_app:'+('a'*64)+'@db/partnerhub',
        'PARTNERTYPE_SESSION_SECRET':'b'*64}
    for key in ('DATABASE_URL_FILE','PARTNERTYPE_SESSION_SECRET_FILE','PARTNERTYPE_ALLOWED_ORIGINS','FLASK_DEBUG'):
        monkeypatch.delenv(key,raising=False)
    for key,value in values.items():monkeypatch.setenv(key,value)
    return monkeypatch


def test_valid_production_configuration(production):
    assert Config.load().environment=='production'


@pytest.mark.parametrize('key,value',[
    ('PARTNERTYPE_ENV',''),('PARTNERTYPE_BASE_URL','http://a.localhost'),
    ('PARTNERTYPE_ALLOWED_ORIGINS','https://a.localhost:8443,garbage'),
    ('DATABASE_URL','sqlite:///database.db'),('PARTNERTYPE_SESSION_SECRET','short'),
    ('FLASK_DEBUG','1'),('PARTNERTYPE_INSTANCE_ID','../a'),
    ('DATABASE_URL','postgresql://partnerhub_owner:'+('a'*64)+'@db/partnerhub')])
def test_invalid_configuration_fails_closed(production,key,value):
    production.setenv(key,value)
    with pytest.raises(ValueError):Config.load()


def test_test_environment_uses_reserved_database_name(tmp_path):
    create(args(env='test'),tmp_path/'test-instance')
    doc=json.loads((tmp_path/'test-instance/compose.json').read_text())
    assert doc['services']['db']['environment']['POSTGRES_DB'].startswith('ph_test_')
    assert '/ph_test_' in (tmp_path/'test-instance/secrets/database_url').read_text()


def test_same_id_in_separate_configuration_roots_never_shares_volumes(tmp_path):
    create(args(),tmp_path/'one'/'producer-a')
    create(args(),tmp_path/'two'/'producer-a')
    one=json.loads((tmp_path/'one/producer-a/compose.json').read_text())
    two=json.loads((tmp_path/'two/producer-a/compose.json').read_text())
    assert one['name'] != two['name']


def test_model_uses_selected_app_image_not_operator_repository(monkeypatch,tmp_path):
    from tools import instance
    calls=[]
    def fake_compose(directory,*command,**kwargs):
        calls.append((command,kwargs))
        return argparse.Namespace(stdout='FROM qwen3:8b\nPARAMETER temperature 0.1\n')
    monkeypatch.setattr(instance,'compose',fake_compose)
    instance.model(tmp_path)
    assert any('app' in command and '/app/Modelfile' in command[-1] for command,_ in calls)
    model_write=[kwargs for command,kwargs in calls if command[-1]=='cat > /tmp/Modelfile'][0]
    assert model_write['input'].startswith('FROM qwen3:8b')


def test_every_service_has_a_memory_ceiling_and_bounded_logs(tmp_path):
    """One producer's instance must not be able to take the host down, and no
    container may grow its log file until the disk is full."""
    create(args(),tmp_path/'a')
    services=json.loads((tmp_path/'a/compose.json').read_text())['services']
    for name,service in services.items():
        assert service['logging']['options']['max-size']=='10m',name
        if name=='migrate':continue  # short-lived operator job, not a resident
        assert service['mem_limit'],name
        assert service['cpus'],name
        assert service['pids_limit'],name
    assert services['ollama']['cpus']>=services['app']['cpus']


def test_resident_services_drop_privileges_they_do_not_need(tmp_path):
    create(args(),tmp_path/'a')
    services=json.loads((tmp_path/'a/compose.json').read_text())['services']
    for name in ('app','ollama','proxy','mail','db'):
        assert 'no-new-privileges:true' in services[name]['security_opt'],name
    for name in ('app','ollama','proxy','mail'):
        assert services[name]['cap_drop']==['ALL'],name
    # Caddy binds 443 inside the container; postgres steps down with gosu.
    assert services['proxy']['cap_add']==['NET_BIND_SERVICE']
    assert 'cap_drop' not in services['db']


def test_upgrade_survives_an_interrupted_previous_attempt(tmp_path):
    """A leftover temporary file used to block every later upgrade."""
    from tools.instance import replace_json
    directory=tmp_path/'a'
    create(args(),directory)
    (directory/'config.json.new').write_text('{"wreckage": true}')

    replace_json(directory,'config.json',{'image':'sha256:'+'b'*64})

    assert json.loads((directory/'config.json').read_text())['image']=='sha256:'+'b'*64
    assert not list(directory.glob('config.json.*.new'))


def test_schema_version_matches_the_newest_migration():
    """A migration added without bumping SCHEMA_VERSION makes /health answer 503
    on a correctly migrated database -- three times during this work it did."""
    import re
    root = Path(__file__).resolve().parents[1]
    revisions = {}
    for path in (root/'migrations/versions').glob('[0-9]*.py'):
        text = path.read_text()
        revision = re.search(r"^revision = '([^']+)'", text, re.M).group(1)
        down = re.search(r"^down_revision = (?:'([^']+)'|None)", text, re.M).group(1)
        revisions[revision] = down
    heads = set(revisions) - {down for down in revisions.values() if down}
    assert len(heads) == 1, f'expected one latest migration, found: {heads}'

    declared = re.search(r"^SCHEMA_VERSION = '([^']+)'",
                         (root/'api/db.py').read_text(), re.M).group(1)
    assert declared == heads.pop()


def test_a_restore_drill_notices_a_copy_that_came_back_short():
    """The drill's verdict rests on this comparison, so it is tested on its
    own: a restore that silently loses rows or money must not pass."""
    from tools.restore_drill import compare
    original = {"rows": {"leads": 10, "users": 3}, "money": {"leads.value": 1000.0}}

    assert compare(original, original) == {}

    short = {"rows": {"leads": 9, "users": 3}, "money": {"leads.value": 1000.0}}
    assert compare(original, short) == {"leads": {"original": 10, "restored": 9}}

    poorer = {"rows": {"leads": 10, "users": 3}, "money": {"leads.value": 900.0}}
    assert compare(original, poorer) == {
        "leads.value": {"original": 1000.0, "restored": 900.0}}


def test_the_operator_mailbox_reaches_every_instance(tmp_path):
    # Mail is the system's, set once for all producers.
    root=tmp_path/'staging'; root.mkdir()
    (root/'mail.json').write_text(json.dumps({'host':'smtp.provider.test','port':587,
        'user':'mailer','from':'no-reply@inpartner.test'}))
    (root/'mail_password').write_text('synthetic-mail-secret\n')
    create(args(),root/'a')
    create(args(id='producer-b',base_url='https://b.localhost:9443',port=9443),root/'b')
    for name in ('a','b'):
        environment=json.loads((root/name/'compose.json').read_text())['services']['app']['environment']
        assert environment['PARTNERTYPE_SMTP_HOST']=='smtp.provider.test'
        assert environment['PARTNERTYPE_SMTP_FROM']=='no-reply@inpartner.test'
        assert 'synthetic-mail-secret' not in json.dumps(environment)
        assert (root/name/'secrets'/'smtp_password').read_text()=='synthetic-mail-secret'
    create(args(),root/'a')  # re-running create stays idempotent with mail set


def test_without_an_operator_mailbox_no_mail_is_configured(tmp_path):
    create(args(),tmp_path/'a')
    environment=json.loads((tmp_path/'a/compose.json').read_text())['services']['app']['environment']
    assert not [key for key in environment if key.startswith('PARTNERTYPE_SMTP_') and not key.endswith('_FILE')]


def test_a_mailbox_file_without_a_host_is_refused(tmp_path):
    (tmp_path/'mail.json').write_text(json.dumps({'user':'mailer'}))
    with pytest.raises(ValueError):create(args(),tmp_path/'a')


def test_a_pilot_instance_sits_behind_the_edge_without_the_assistant(tmp_path):
    create(args(base_url='https://firma.inpartner.test',port=18001,edge=True,no_ai=True),tmp_path/'a')
    config=json.loads((tmp_path/'a/config.json').read_text())
    assert config['edge'] is True and config['ai'] is False
    services=json.loads((tmp_path/'a/compose.json').read_text())['services']
    # Still only on localhost; the edge is the public side, plain HTTP inside.
    assert services['proxy']['ports']==['127.0.0.1:18001:80']
    assert services['app']['environment']['PARTNERTYPE_PROXY_HOPS']=='2'
    assert services['app']['environment']['PARTNERTYPE_AI']=='off'
    assert services['ollama']['mem_limit']=='256m'
    caddy=(tmp_path/'a/Caddyfile').read_text()
    assert 'http://firma.inpartner.test:80' in caddy and 'tls internal' not in caddy
    assert 'trusted_proxies static private_ranges' in caddy


def test_the_default_instance_is_unchanged_by_the_new_options(tmp_path):
    create(args(),tmp_path/'a')
    config=json.loads((tmp_path/'a/config.json').read_text())
    assert 'edge' not in config and 'ai' not in config
    services=json.loads((tmp_path/'a/compose.json').read_text())['services']
    assert services['app']['environment']['PARTNERTYPE_PROXY_HOPS']=='1'
    assert services['app']['environment']['PARTNERTYPE_AI']=='on'
    assert services['ollama']['mem_limit']=='10g'
    assert 'tls internal' in (tmp_path/'a/Caddyfile').read_text()


def test_behind_the_edge_the_public_url_carries_no_port(tmp_path):
    with pytest.raises(ValueError):
        create(args(base_url='https://firma.inpartner.test:8443',port=8443,edge=True),tmp_path/'a')


def test_the_edge_routes_each_producer_to_its_own_instance_only(tmp_path):
    from tools.edge import write
    root=tmp_path/'production'
    create(args(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True,no_ai=True),root/'firma-a')
    create(args(env='production',id='firma-b',base_url='https://b.inpartner.test',port=18002,edge=True),root/'firma-b')
    create(args(env='production',id='lokalna',base_url='https://c.localhost:8443',port=8443),root/'lokalna')
    directory,found=write(root,'ops@inpartner.test')
    assert found==[('a.inpartner.test',18001),('b.inpartner.test',18002)]
    caddy=(directory/'Caddyfile').read_text()
    assert 'a.inpartner.test {\n    reverse_proxy 127.0.0.1:18001' in caddy
    assert 'b.inpartner.test {\n    reverse_proxy 127.0.0.1:18002' in caddy
    assert 'c.localhost' not in caddy and 'tls internal' not in caddy
    edge=json.loads((directory/'compose.json').read_text())['services']['edge']
    assert edge['network_mode']=='host' and edge['cap_add']==['NET_BIND_SERVICE']


def test_the_edge_refuses_two_producers_on_one_port(tmp_path):
    from tools.edge import write
    root=tmp_path/'production'
    create(args(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True),root/'firma-a')
    # create itself refuses the clash now; a hand-edited config still meets the edge's check.
    (root/'firma-b').mkdir()
    (root/'firma-b'/'config.json').write_text(json.dumps({'id':'firma-b','edge':True,
        'base_url':'https://b.inpartner.test','port':18001}))
    with pytest.raises(ValueError):write(root,'ops@inpartner.test')


@pytest.mark.parametrize('clash',[dict(port=18001),dict(base_url='https://a.inpartner.test')])
def test_create_refuses_an_address_or_port_already_taken(tmp_path,clash):
    from tools.edge import write
    from tools.instance import update_config
    root=tmp_path/'production'
    (root).mkdir()
    create(args(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True),root/'firma-a')
    # Even once destroyed, the address stays taken: create may bring it back.
    update_config(root/'firma-a',destroyed=True)
    options=dict(env='production',id='firma-b',base_url='https://b.inpartner.test',port=18002,edge=True)
    options.update(clash)
    with pytest.raises(ValueError):
        create(args(**options),root/'firma-b')
    assert sorted(p.name for p in root.iterdir())==['firma-a']
    write(root,'ops@inpartner.test')  # nothing left behind to block the edge


def test_leftover_staging_and_drill_directories_are_not_instances(tmp_path):
    from tools.edge import write
    root=tmp_path/'production'
    create(args(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True),
           root/'firma-a')
    for leftover in ('.prepare-abc123','.drill-firma-a-0011aabb'):
        (root/leftover).mkdir()
        (root/leftover/'config.json').write_text((root/'firma-a'/'config.json').read_text())
    assert write(root,'ops@inpartner.test')[1]==[('a.inpartner.test',18001)]
    create(args(env='production',id='firma-b',base_url='https://b.inpartner.test',port=18002,edge=True),root/'firma-b')


def test_the_edge_drops_a_destroyed_instance(tmp_path):
    from tools.edge import write
    from tools.instance import update_config
    root=tmp_path/'production'
    for n,name in enumerate(('aktywna','usunieta')):
        create(args(env='production',id=name,base_url=f'https://{name}.inpartner.test',port=18001+n,edge=True),root/name)
    update_config(root/'usunieta',destroyed=True)
    _,found=write(root,'ops@inpartner.test')
    assert sorted(found)==[('aktywna.inpartner.test',18001)]


def test_the_proxy_compresses_pages_and_scripts_but_not_api_answers():
    # Pages, stylesheets and scripts go out compressed; API answers
    # stay as they are, so a secret beside injected text cannot leak through
    # the compressed length (BREACH). Both ways an instance can stand.
    from tools.instance import caddyfile
    for edge in (False, True):
        caddy = caddyfile('a.inpartner.test', edge, 'app:8000')
        encode = caddy[caddy.index('encode {'):caddy.index('reverse_proxy')]
        assert 'gzip' in encode and 'zstd' in encode
        assert 'text/html' in encode and 'text/css' in encode and 'javascript' in encode
        assert 'application/json' not in encode


GATEWAY_VARIABLES = {'PARTNERTYPE_API_RATE_PER_MINUTE', 'PARTNERTYPE_API_RATE_PER_DAY',
                     'PARTNERTYPE_OAUTH_REGISTER_PER_HOUR', 'PARTNERTYPE_MCP_EXTRA_ORIGINS'}


def test_the_proxy_passes_every_path_to_the_app_unchanged():
    # /mcp, /.well-known/*, /oauth/* and /api/v1/* must
    # reach Flask as they are. A path matcher in either generated Caddyfile
    # could cut the API gateway off, so there is none, and one reverse_proxy.
    from tools.instance import caddyfile
    for edge in (False, True):
        caddy = caddyfile('a.inpartner.test', edge, 'app:8000')
        for keyword in ('handle', 'route', 'path '):
            assert keyword not in caddy, (edge, keyword)
        directives = {line.split()[0] for line in caddy.splitlines() if line.strip()}
        assert not directives & {'rewrite', 'uri', 'redir', 'respond', 'error', 'abort'}, edge
        lines = [line.strip() for line in caddy.splitlines() if 'reverse_proxy' in line]
        assert lines == ['reverse_proxy app:8000'], (edge, lines)


def test_the_gateway_limits_reach_the_app_only_when_the_instance_sets_them(tmp_path):
    from deploy.compose import document
    create(args(), tmp_path/'a')
    config = json.loads((tmp_path/'a/config.json').read_text())
    environment = document(config, tmp_path/'a')['services']['app']['environment']
    assert not GATEWAY_VARIABLES & set(environment)

    config['api'] = {'rate_per_minute': 30, 'rate_per_day': 2000, 'register_per_hour': 5,
                     'mcp_extra_origins': ['https://claude.ai', 'https://claude.com']}
    environment = document(config, tmp_path/'a')['services']['app']['environment']
    assert {name: environment[name] for name in GATEWAY_VARIABLES} == {
        'PARTNERTYPE_API_RATE_PER_MINUTE': '30',
        'PARTNERTYPE_API_RATE_PER_DAY': '2000',
        'PARTNERTYPE_OAUTH_REGISTER_PER_HOUR': '5',
        'PARTNERTYPE_MCP_EXTRA_ORIGINS': 'https://claude.ai,https://claude.com',
    }

    # One key set, the others keep the app's defaults.
    config['api'] = {'rate_per_minute': 10}
    environment = document(config, tmp_path/'a')['services']['app']['environment']
    assert GATEWAY_VARIABLES & set(environment) == {'PARTNERTYPE_API_RATE_PER_MINUTE'}


def test_dollar_signs_reach_the_instance_as_written_not_from_the_operator_shell(tmp_path):
    """Compose substitutes $VAR in every string; outside values are escaped."""
    create(args(name='Cash$ Ltd ${AWS_SECRET_ACCESS_KEY}'),tmp_path/'a')
    document=json.loads((tmp_path/'a/compose.json').read_text())
    environment=document['services']['app']['environment']
    assert environment['PARTNERTYPE_INSTANCE_NAME']=='Cash$$ Ltd $${AWS_SECRET_ACCESS_KEY}'
    # The instance's own record keeps the name exactly as typed.
    assert json.loads((tmp_path/'a/config.json').read_text())['name']=='Cash$ Ltd ${AWS_SECRET_ACCESS_KEY}'
    # No single `$` survives anywhere in the generated file.
    import re
    assert not re.search(r'(?<!\$)\$(?!\$)',json.dumps(document).replace('$$',''))


def test_the_edge_document_escapes_dollar_signs_too(tmp_path):
    from tools import edge
    directory=tmp_path/'odd$dir'/'edge'
    assert '$$' in json.dumps(edge.compose_document(directory))


def test_the_proxy_can_read_its_caddyfile_without_extra_capabilities(tmp_path):
    """Caddy runs with every capability dropped: an operator-only file is unreadable."""
    import os
    from tools.instance import write_caddyfile
    old=os.umask(0o077)
    try:
        create(args(),tmp_path/'a')
        assert (tmp_path/'a/Caddyfile').stat().st_mode & 0o777==0o644
        # An instance written by an older version, rewritten by upgrade.
        (tmp_path/'a/Caddyfile').chmod(0o600)
        write_caddyfile(tmp_path/'a',json.loads((tmp_path/'a/config.json').read_text()))
    finally:
        os.umask(old)
    assert (tmp_path/'a/Caddyfile').stat().st_mode & 0o777==0o644
    assert 'tls internal' in (tmp_path/'a/Caddyfile').read_text()


@pytest.mark.parametrize('name',['edge','mail'])
def test_the_reserved_names_cannot_be_taken_by_an_instance(tmp_path,name):
    with pytest.raises(ValueError):
        create(args(id=name,base_url=f'https://{name}.inpartner.test',port=18001,edge=True),tmp_path/name)
    assert not (tmp_path/name).exists()


def test_the_cli_refuses_a_reserved_instance_id(tmp_path,monkeypatch):
    import sys
    from tools import instance
    monkeypatch.setattr(sys,'argv',['instance','--root',str(tmp_path),'--env','production','--id','edge','stop'])
    monkeypatch.setattr(instance,'compose',lambda *a,**k:pytest.fail('compose must not run'))
    with pytest.raises(SystemExit) as refused:
        instance.main()
    assert refused.value.code==2


def test_the_edge_refuses_to_overwrite_an_instance_living_in_its_directory(tmp_path):
    from tools.edge import write
    root=tmp_path/'production'
    (root/'edge').mkdir(parents=True)
    (root/'edge'/'config.json').write_text(json.dumps({'id':'edge','edge':True,'base_url':'https://edge.inpartner.test','port':18001}))
    with pytest.raises(ValueError):
        write(root,'ops@inpartner.test')
    assert not (root/'edge'/'Caddyfile').exists()


class FakeDocker:
    """Records compose calls; answers the bootstrap like the real image does."""

    def __init__(self):
        self.calls=[]

    def __call__(self,directory,*arguments,**kwargs):
        import types
        self.calls.append(arguments)
        stdout=json.dumps({'created':False}) if 'tools.bootstrap' in arguments else ''
        return types.SimpleNamespace(stdout=stdout,returncode=0)


@pytest.mark.parametrize('prepare_only',[True,False])
def test_creating_a_destroyed_instance_again_makes_it_reachable(tmp_path,monkeypatch,prepare_only):
    from tools import instance
    from tools.edge import write
    root=tmp_path/'production'
    options=dict(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True,no_ai=True)
    create(args(**options),root/'firma-a')
    instance.update_config(root/'firma-a',destroyed=True)
    assert write(root,'ops@inpartner.test')[1]==[]

    monkeypatch.setattr(instance,'compose',FakeDocker())
    create(args(**options,prepare_only=prepare_only),root/'firma-a')

    assert 'destroyed' not in json.loads((root/'firma-a/config.json').read_text())
    assert write(root,'ops@inpartner.test')[1]==[('a.inpartner.test',18001)]


def test_a_failed_rebuild_leaves_the_instance_marked_destroyed(tmp_path,monkeypatch):
    import subprocess
    from tools import instance
    root=tmp_path/'production'
    options=dict(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True,no_ai=True)
    create(args(**options),root/'firma-a')
    instance.update_config(root/'firma-a',destroyed=True)

    def failing(directory,*arguments,**kwargs):
        raise subprocess.CalledProcessError(1,'docker')
    monkeypatch.setattr(instance,'compose',failing)
    with pytest.raises(subprocess.CalledProcessError):
        create(args(**options,prepare_only=False),root/'firma-a')
    assert json.loads((root/'firma-a/config.json').read_text())['destroyed'] is True


def run_cli(monkeypatch,root,*arguments):
    import sys
    from tools import instance
    docker=FakeDocker()
    monkeypatch.setattr(instance,'compose',docker)
    monkeypatch.setattr(sys,'argv',['instance','--root',str(root),'--env','production','--id','firma-a',*arguments])
    instance.main()
    return docker.calls


def lifecycle_instance(tmp_path,**state):
    from tools.instance import update_config
    root=tmp_path/'instances'
    create(args(env='production',id='firma-a',base_url='https://a.inpartner.test',port=18001,edge=True,no_ai=True),
           root/'production'/'firma-a')
    update_config(root/'production'/'firma-a',**state)
    return root


@pytest.mark.parametrize('command',[['upgrade','--image','sha256:'+'b'*64],['mail'],
                                     ['migrate'],['start'],['restore','x.age','--identity','k'],
                                     ['model'],['maintenance'],
                                     ['knowledge-quota','--default']])
def test_a_destroyed_instance_refuses_everything_that_would_start_it(tmp_path,monkeypatch,command):
    root=lifecycle_instance(tmp_path,destroyed=True)
    with pytest.raises(ValueError):
        run_cli(monkeypatch,root,*command)


def test_a_destroyed_instance_can_still_be_stopped_and_destroyed_again(tmp_path,monkeypatch):
    root=lifecycle_instance(tmp_path,destroyed=True)
    assert run_cli(monkeypatch,root,'stop')==[('down',)]
    assert run_cli(monkeypatch,root,'destroy-data','--confirm-id','firma-a')==[('down','--volumes')]


@pytest.mark.parametrize('periods',['x=1".split(",") if 0 else __import__("os").system("id") or "',
                                    'notifications=1\\','notifications=abc','notifications=0',
                                    'Notifications=30','notifications=-5','=30'])
def test_retention_periods_that_are_not_plain_pairs_are_refused(periods):
    from tools.instance import parse_periods
    with pytest.raises(ValueError):
        parse_periods(periods)


def test_retention_periods_reach_the_container_as_data():
    import ast
    from tools.instance import parse_periods,retention_script
    periods=parse_periods('notifications=180, audit_log=730,')
    assert periods=={'notifications':180,'audit_log':730}
    tree=ast.parse(retention_script(periods,False))
    assigned={node.targets[0].id:ast.literal_eval(node.value) for node in tree.body
              if isinstance(node,ast.Assign)}
    assert assigned=={'periods':periods,'apply':False}
    assert parse_periods(None)=={} and parse_periods('')=={}


def test_the_cli_refuses_bad_retention_periods_before_running_anything(tmp_path,monkeypatch):
    root=lifecycle_instance(tmp_path)
    with pytest.raises(SystemExit) as refused:
        run_cli(monkeypatch,root,'retention','--periods','x=1"+__import__("os").system("id")+"')
    assert refused.value.code==2


class FakeDump:
    """pg_dump as seen through Popen: a pipe and an exit status."""

    def __init__(self,status):
        import io
        self.stdout=io.BytesIO(b'PGDMP partial')
        self.status=status

    def wait(self):
        return self.status

    def poll(self):
        return self.status


def fake_backup_tools(monkeypatch,dump_status,age_status=0):
    import subprocess,types
    from tools import instance
    monkeypatch.setattr(instance.subprocess,'Popen',lambda *a,**k:FakeDump(dump_status))

    def age(command,stdin,stdout):
        stdout.write(b'age-encryption.org/v1 ...')
        return types.SimpleNamespace(returncode=age_status)
    monkeypatch.setattr(instance.subprocess,'run',age)


@pytest.mark.parametrize('dump_status,age_status',[(1,0),(0,1)])
def test_a_failed_backup_leaves_no_file_that_looks_like_a_backup(tmp_path,monkeypatch,dump_status,age_status):
    from tools.instance import backup
    create(args(),tmp_path/'a')
    (tmp_path/'out').mkdir()
    fake_backup_tools(monkeypatch,dump_status,age_status)
    with pytest.raises(RuntimeError):
        backup(tmp_path/'a',str(tmp_path/'out'/'a.dump.age'),'age1recipient')
    assert list((tmp_path/'out').iterdir())==[]


def test_a_complete_backup_appears_under_its_name_private_and_never_overwrites(tmp_path,monkeypatch):
    from tools.instance import backup
    create(args(),tmp_path/'a')
    (tmp_path/'out').mkdir()
    fake_backup_tools(monkeypatch,0)
    target=tmp_path/'out'/'a.dump.age'
    backup(tmp_path/'a',str(target),'age1recipient')
    assert [p.name for p in (tmp_path/'out').iterdir()]==['a.dump.age']
    assert target.stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError):
        backup(tmp_path/'a',str(target),'age1recipient')


class FakeRestoreDocker(FakeDocker):
    def __init__(self,enrolled=''):
        super().__init__()
        self.enrolled=enrolled

    def __call__(self,directory,*arguments,**kwargs):
        import types
        self.calls.append(arguments)
        query=arguments[-1] if arguments else ''
        if 'information_schema.tables' in query:
            return types.SimpleNamespace(stdout='0\n',returncode=0)
        if 'user_mfa' in query:
            return types.SimpleNamespace(stdout=self.enrolled,returncode=0)
        return types.SimpleNamespace(stdout='',returncode=0)


def fake_decryptor(monkeypatch):
    from tools import instance
    monkeypatch.setattr(instance.subprocess,'Popen',lambda *a,**k:FakeDump(0))


def test_a_restore_can_keep_the_original_session_secret_so_authenticator_apps_still_work(tmp_path,monkeypatch,capsys):
    from tools import instance
    create(args(),tmp_path/'original')
    create(args(id='restored',base_url='https://r.localhost:9443',port=9443),tmp_path/'restored')
    original=(tmp_path/'original/secrets/session_secret').read_text()
    assert (tmp_path/'restored/secrets/session_secret').read_text()!=original
    monkeypatch.setattr(instance,'compose',FakeRestoreDocker('owner@example.test\n'))
    fake_decryptor(monkeypatch)

    instance.restore(tmp_path/'restored','dump.age','identity',tmp_path/'original/secrets/session_secret')

    secret=tmp_path/'restored/secrets/session_secret'
    assert secret.read_text()==original and secret.stat().st_mode & 0o777==0o444
    assert 'WARNING' not in capsys.readouterr().out


def test_a_restore_with_a_new_session_secret_names_the_users_who_lose_their_second_factor(tmp_path,monkeypatch,capsys):
    from tools import instance
    create(args(),tmp_path/'restored')
    monkeypatch.setattr(instance,'compose',FakeRestoreDocker('admin@example.test\nowner@example.test\n'))
    fake_decryptor(monkeypatch)
    instance.restore(tmp_path/'restored','dump.age','identity')
    out=capsys.readouterr().out
    assert 'WARNING' in out and 'mfa-reset' in out
    assert '  admin@example.test\n  owner@example.test\n' in out


def test_a_file_that_is_not_a_session_secret_is_refused_before_the_restore(tmp_path,monkeypatch):
    from tools import instance
    create(args(),tmp_path/'restored')
    before=(tmp_path/'restored/secrets/session_secret').read_text()
    (tmp_path/'wrong').write_text('postgresql://user:pass@db/x\n')
    docker=FakeRestoreDocker()
    monkeypatch.setattr(instance,'compose',docker)
    with pytest.raises(ValueError):
        instance.restore(tmp_path/'restored','dump.age','identity',tmp_path/'wrong')
    assert (tmp_path/'restored/secrets/session_secret').read_text()==before
    assert ('stop','app','proxy') not in docker.calls


def test_the_edge_admin_api_is_not_on_the_hosts_network(tmp_path):
    from tools.edge import ADMIN,caddyfile
    caddy=caddyfile('ops@inpartner.test',[('a.inpartner.test',18001)])
    assert '    admin unix//data/admin.sock\n' in caddy and ADMIN.startswith('unix//data/')
    assert ':2019' not in caddy


def test_the_release_image_ships_no_tests_demo_seeds_or_test_tools():
    """The same image runs in every environment; a demo admin with a public
    password must not be one command away on any of them."""
    import re
    text=(Path(__file__).resolve().parents[1]/'Dockerfile').read_text()
    stages=dict(re.findall(r'(?ms)^FROM \S+ AS (\S+)\n(.*?)(?=^FROM |\Z)',text))
    pruned=stages['release-source']
    for path in ('api/tests','tools/test_database.py','deploy/test-init.sh','js/__tests__'):
        assert path in pruned,path
    runtime=stages['runtime']
    assert 'seed_demo' not in runtime
    for source in ('api/','tools/','deploy/'):
        # Only through the pruned stage, never straight from the build context.
        assert not re.search(r'(?m)^COPY '+re.escape(source)+' ',runtime),source
        assert 'COPY --from=release-source /src/'+source in runtime
    assert re.search(r'(?m)^FROM runtime AS release',text)
    # The test image keeps everything the suite needs.
    test=stages['test']
    assert re.search(r'(?m)^COPY api/ api/',test) and re.search(r'(?m)^COPY tools/ tools/',test)
    assert 'COPY scripts/seed_demo.py' in test


def test_the_local_agent_setup_pulls_the_base_model_the_modelfile_names(tmp_path):
    import os,re,subprocess
    root=Path(__file__).resolve().parents[1]
    fake=tmp_path/'ollama'
    fake.write_text('#!/bin/sh\necho "ollama $*"\n'); fake.chmod(0o755)
    result=subprocess.run(['sh',str(root/'scripts/setup-local-agent.sh')],capture_output=True,text=True,
                          env={**os.environ,'PATH':f'{tmp_path}:{os.environ["PATH"]}'},check=True)
    base=re.search(r'(?m)^\s*FROM\s+(\S+)\s*$',(root/'Modelfile').read_text()).group(1)
    assert f'ollama pull {base}\n' in result.stdout
    assert 'qwen' not in (root/'scripts/setup-local-agent.sh').read_text()


def test_the_security_scan_pins_its_scanners_and_never_hands_out_the_docker_socket():
    import re
    script=(Path(__file__).resolve().parents[1]/'scripts/security-scan.sh').read_text()
    assert 'docker.sock' not in script and ':latest' not in script
    assert re.search(r"TRIVY_IMAGE='aquasec/trivy:[0-9.]+@sha256:[0-9a-f]{64}'",script)
    assert re.search(r"PIP_AUDIT_VERSION='[0-9.]+'",script) and "pip-audit==$PIP_AUDIT_VERSION" in script
    assert '--input /scan/image.tar' in script


def test_the_operator_sets_the_knowledge_base_space(tmp_path):
    from tools.instance import update_config
    create(args(base_url='https://firma.inpartner.test',port=18001,edge=True,no_ai=True),tmp_path/'a')
    read=lambda:json.loads((tmp_path/'a/compose.json').read_text())['services']['app']['environment']
    assert 'PARTNERTYPE_KNOWLEDGE_QUOTA_MB' not in read()
    update_config(tmp_path/'a',knowledge_quota_mb=500)
    assert read()['PARTNERTYPE_KNOWLEDGE_QUOTA_MB']=='500'
    # Changing the quota later is not a different configuration for create.
    create(args(base_url='https://firma.inpartner.test',port=18001,edge=True,no_ai=True),tmp_path/'a')
    update_config(tmp_path/'a',knowledge_quota_mb=None)
    assert 'PARTNERTYPE_KNOWLEDGE_QUOTA_MB' not in read()


def test_the_knowledge_quota_command_restarts_the_app_with_the_new_space(tmp_path,monkeypatch):
    root=lifecycle_instance(tmp_path)
    directory=root/'production'/'firma-a'
    read=lambda:json.loads((directory/'compose.json').read_text())['services']['app']['environment']
    calls=run_cli(monkeypatch,root,'knowledge-quota','--mb','500')
    assert json.loads((directory/'config.json').read_text())['knowledge_quota_mb']==500
    assert read()['PARTNERTYPE_KNOWLEDGE_QUOTA_MB']=='500'
    assert calls==[('up','-d','--force-recreate','--no-deps','app')]
    run_cli(monkeypatch,root,'knowledge-quota','--default')
    assert 'knowledge_quota_mb' not in json.loads((directory/'config.json').read_text())
    assert 'PARTNERTYPE_KNOWLEDGE_QUOTA_MB' not in read()


@pytest.mark.parametrize('value',['0','1048577','-5'])
def test_the_knowledge_quota_command_refuses_a_space_out_of_range(tmp_path,monkeypatch,value):
    root=lifecycle_instance(tmp_path)
    with pytest.raises(ValueError):
        run_cli(monkeypatch,root,'knowledge-quota','--mb',value)
    assert 'knowledge_quota_mb' not in json.loads((root/'production/firma-a/config.json').read_text())
