"""The public HTTPS entrance to a server's PartnerType instance.

An instance created with `tools.instance create --edge` listens on localhost
only. This proxy is the one service that faces the internet: it holds ports
80 and 443, obtains a real certificate for the instance's address (Let's
Encrypt, through Caddy) and hands each request to it by host name. A host
name that is not listed gets no answer; a destroyed instance is dropped.

If you already run a reverse proxy, you do not need this: create the instance
with --edge and point your proxy at its localhost port instead.

    python3 -m tools.edge --env production --email ops@example.com write
    python3 -m tools.edge --env production --email ops@example.com start
    python3 -m tools.edge --env production --email ops@example.com reload

`write` only regenerates instances/<env>/edge/; `start` also brings the proxy
up, and `reload` makes a running proxy pick up a newly created instance
without dropping the others' connections.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

from deploy.compose import literal

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'caddy:2.10.2'
# Caddy's admin API, which can replace the whole configuration. The edge uses
# the host's network, where the default localhost:2019 would let any local
# user or process reroute the traffic or read the certificates' keys; a
# socket inside the container's data volume is reachable only through
# `docker compose exec`.
ADMIN = 'unix//data/admin.sock'
LOGGING = {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '5'}}


def _host(value, where):
    if not value or not re.fullmatch(r'[a-z0-9.-]+', value):
        raise ValueError('Invalid host name in ' + str(where))
    return value


def routes(environment_root):
    """(host name, localhost port) for every address the edge answers."""
    found, instance_ports = [], []
    for config_file in sorted(environment_root.glob('*/config.json')):
        if config_file.parent.name.startswith('.'):
            continue  # staging directories and restore-drill copies
        config = json.loads(config_file.read_text())
        if not config.get('edge') or config.get('destroyed'):
            continue
        host = _host(urlsplit(config['base_url']).hostname, config_file)
        instance_ports.append(int(config['port']))
        found.append((host, int(config['port'])))
    hosts = [host for host, _ in found]
    if len(set(hosts)) != len(hosts) or len(set(instance_ports)) != len(instance_ports):
        # Two instances on one address or one port would see each other's data.
        raise ValueError('Two instances share a host name or a port; nothing written')
    return found


def caddyfile(email, found):
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise ValueError('An e-mail address for the certificate authority is required')
    lines = ['{', '    email ' + email, '    admin ' + ADMIN,
             # The access log keeps no request details, as in each instance.
             '    log default {', '        format filter {', '            wrap json',
             '            request delete', '            resp_headers delete',
             '        }', '    }', '}', '']
    for host, port in found:
        lines += [host + ' {',
                  '    reverse_proxy 127.0.0.1:' + str(port),
                  '    header Strict-Transport-Security "max-age=31536000"',
                  '}', '']
    return '\n'.join(lines)


def compose_document(directory):
    return literal({'name': 'ph-edge-' + directory.parent.name, 'services': {'edge': {
        'image': IMAGE, 'restart': 'unless-stopped',
        # Host networking: the instances listen on the host's 127.0.0.1.
        'network_mode': 'host',
        'cap_drop': ['ALL'], 'cap_add': ['NET_BIND_SERVICE'],
        'security_opt': ['no-new-privileges:true'],
        'volumes': [str(directory/'Caddyfile')+':/etc/caddy/Caddyfile:ro', 'certificates:/data'],
        'mem_limit': '256m', 'cpus': 1.0, 'pids_limit': 256, 'logging': LOGGING}},
        'volumes': {'certificates': {}}})


def write(environment_root, email):
    directory = environment_root/'edge'
    if (directory/'config.json').exists():
        # An instance created under the edge's own name before the name was
        # reserved: writing here would replace its files and make its stop
        # or destroy-data act on the edge.
        raise ValueError('instances/<env>/edge holds an instance; move it before using the edge')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    found = routes(environment_root)
    (directory/'Caddyfile').write_text(caddyfile(email, found))
    (directory/'compose.json').write_text(json.dumps(compose_document(directory), indent=2))
    return directory, found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT/'instances')
    parser.add_argument('--env', required=True, choices=['staging', 'production'])
    parser.add_argument('--email', required=True, help='Contact for Let\'s Encrypt about the certificates')
    parser.add_argument('command', choices=['write', 'start', 'reload'])
    args = parser.parse_args()
    directory, found = write(args.root.resolve()/args.env, args.email)
    for host, port in found:
        print(f'https://{host} -> 127.0.0.1:{port}')
    compose = ['docker', 'compose', '-f', str(directory/'compose.json')]
    if args.command == 'start':
        subprocess.run([*compose, 'up', '-d'], check=True)
    elif args.command == 'reload':
        subprocess.run([*compose, 'exec', '-T', 'edge', 'caddy', 'reload',
                        '--config', '/etc/caddy/Caddyfile', '--address', ADMIN], check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(str(error) if isinstance(error, ValueError) else 'Edge command failed', file=sys.stderr)
        sys.exit(1)
