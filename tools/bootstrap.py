"""Create exactly one pending owner; raw invitation is returned only once.

With {"reinvite": true} it instead gives an owner who never set a password a
fresh link (the old one stops working), for an owner who lost the first e-mail
or let it run out (`tools.instance owner-invite`). An owner who
already signed in gets {"created": false} -- "forgot password" is theirs.
"""
import json
import os
import secrets
from datetime import datetime, timedelta
import bcrypt
from api.db import get_conn
from api.postgres import system_context
from api.invitations import _hash_token, build_invite_link
from api.validation import parse_email, parse_required_string


def _new_invitation(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute('INSERT INTO partner_invitations(token_hash,user_id,expires_at) VALUES (?,?,?)',
        (_hash_token(token), user_id, (datetime.utcnow()+timedelta(days=7)).isoformat()))
    return token


@system_context("operator: owner re-invitation")
def reinvite():
    with get_conn() as conn:
        conn.begin()
        owner = conn.execute("SELECT id, account_status FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if not owner or owner['account_status'] != 'pending':
            print(json.dumps({'created': False}))
            return
        conn.execute('DELETE FROM partner_invitations WHERE user_id = ? AND accepted_at IS NULL', (owner['id'],))
        token = _new_invitation(conn, owner['id'])
    print(json.dumps({'created': True, 'invite_url': build_invite_link(token)}))


# Operator command run on the server: it must see every account, or the
# "is there an owner already?" check would always answer no.
@system_context("operator: create the first owner")
def main():
    data = json.loads(input())
    if data.get('reinvite'):
        reinvite()
        return
    email = parse_email(data['email'], 'Email').lower()
    name = parse_required_string(data['name'], 'Nazwa', max_length=200)
    with get_conn() as conn:
        conn.begin()
        conn.execute('SELECT pg_advisory_xact_lock(734102621)')
        if conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            print(json.dumps({'created': False}))
            return
        password = bcrypt.hashpw(secrets.token_bytes(32).hex().encode(), bcrypt.gensalt()).decode()
        user_id = conn.execute("INSERT INTO users(email,password,name,role,company,account_status,active) VALUES (?,?,?,'admin',?,'pending',0)",
            (email,password,name,os.environ['PARTNERTYPE_INSTANCE_NAME'])).lastrowid
        token = _new_invitation(conn, user_id)
        conn.execute("INSERT INTO settings(key,value) VALUES ('company_name',?) ON CONFLICT(key) DO NOTHING", (os.environ['PARTNERTYPE_INSTANCE_NAME'],))
    print(json.dumps({'created': True, 'invite_url': build_invite_link(token)}))


if __name__ == '__main__':
    main()
