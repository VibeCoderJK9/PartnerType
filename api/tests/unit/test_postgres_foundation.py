"""PostgreSQL transaction, privilege, provisioning and HTTP regression checks."""
from api.tests.context import carry_context
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from api.db import get_conn, lead_workflow_transaction, StaleRevisionError
from api import agent
from api.config import Config


def test_concurrent_lead_writers_have_one_winner(sample_lead, admin_user):
    barrier=threading.Barrier(2)
    def write(i):
        with get_conn() as conn:
            barrier.wait(timeout=5)
            try:
                with lead_workflow_transaction(conn,sample_lead['id'],admin_user['id'],0,'race-'+str(i)) as ctx:
                    ctx.update({'notes':str(i)})
                return 'saved'
            except StaleRevisionError:return 'stale'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(carry_context(write),range(2)))==['saved','stale']


def test_concurrent_payouts_cannot_pay_twice(sample_lead, admin_user, partner_user):
    from api.payouts import record_payout
    with get_conn() as conn:
        conn.execute("UPDATE leads SET status='won', commission=700, won_at='2026-09-16', win_confirmed_at='2026-09-16' WHERE id=?",(sample_lead['id'],))
    barrier=threading.Barrier(2)
    def pay(i):
        barrier.wait(timeout=5)
        try:
            return record_payout({'partner_id':partner_user['id'],'period_type':'month','period_key':'2026-09'},admin_user)['amount']
        except ValueError:return 0
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(carry_context(pay),range(2)))==700
    with get_conn() as conn:
        assert conn.execute('SELECT count(*) FROM commission_payouts').fetchone()[0]==1


def test_ai_busy_does_not_block_crm(admin_user, monkeypatch):
    from server import create_app
    entered=threading.Event();release=threading.Event()
    def fake_chat(*args):
        entered.set();assert release.wait(5)
        return 200, {'reply':'synthetic'}
    monkeypatch.setattr(agent,'_handle_chat',fake_chat)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(agent.handle_chat,None,admin_user,{'prompt':'test'})
        try:
            assert entered.wait(2)
            assert agent.handle_chat(None,admin_user,{'prompt':'test'})[0]==429
            assert create_app().test_client().get('/health').status_code==200
        finally:release.set()
        assert future.result()[0]==200


def test_configuration_is_fail_closed(monkeypatch):
    monkeypatch.delenv('PARTNERTYPE_ENV')
    with pytest.raises(ValueError):Config.load()
    monkeypatch.setenv('PARTNERTYPE_ENV','production')
    with pytest.raises(ValueError,match='HTTPS'):Config.load()


def test_schema_mismatch_is_not_ready(monkeypatch):
    from psycopg import connect
    import os
    from server import create_app
    with connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
        conn.execute("UPDATE alembic_version SET version_num='future'")
    assert create_app().test_client().get('/health').status_code==503


def test_migration_is_idempotent_and_does_not_seed_customers():
    import os
    from tools.test_database import migrate
    migrate(os.environ['MIGRATION_DATABASE_URL'])
    migrate(os.environ['MIGRATION_DATABASE_URL'])
    with get_conn() as conn:
        assert conn.execute('SELECT count(*) FROM users').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM lead_stages').fetchone()[0]==6


def test_owner_invitation_to_commission_and_payout(monkeypatch, capsys):
    import json
    from urllib.parse import urlsplit, parse_qs
    from datetime import datetime
    from tools.bootstrap import main
    from api.auth import login
    from api.invitations import accept_invitation, create_invitation
    from api.leads import create_lead, update_lead
    from api.payouts import confirm_wins, record_payout
    monkeypatch.setattr('builtins.input',lambda:json.dumps({'email':'owner@example.test','name':'Owner'}))
    main()
    invitation=json.loads(capsys.readouterr().out)
    token=parse_qs(urlsplit(invitation['invite_url']).query)['token'][0]
    accept_invitation(token,'OwnerPassword123!')
    owner,_=login('owner@example.test','OwnerPassword123!','admin')
    with pytest.raises(ValueError):accept_invitation(token,'DifferentPassword123!')
    partner,token=create_invitation({'email':'partner@example.test','name':'Partner','company':'Synthetic','role':'partner','tier_id':1},owner)
    accept_invitation(token,'PartnerPassword123!')
    partner,_=login('partner@example.test','PartnerPassword123!','partner')
    lead=create_lead(partner,{'client_name':'Client','client_email':'client@example.test','company':'Synthetic','value':10000})
    update_lead(lead['id'],owner,{'registration_status':'accepted'})
    won=update_lead(lead['id'],owner,{'status':'won'})
    confirm_wins([lead['id']],owner)
    receipt=record_payout({'partner_id':partner['id'],'period_type':'month','period_key':datetime.utcnow().strftime('%Y-%m')},owner)
    assert receipt['amount']==won['commission']
    assert receipt['amount']>0
