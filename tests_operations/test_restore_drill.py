"""The restore drill's orchestration: what it may create and what it may delete.

Docker is replaced by a recorder; the drill itself is replaced where a test
needs it to fail or succeed at a given point.
"""
import json
import sys

import pytest

from tools import restore_drill


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(restore_drill, 'ROOT', tmp_path)
    root = tmp_path/'instances'/'production'
    for name in ('acme', 'acme-drill'):
        (root/name).mkdir(parents=True)
        (root/name/'config.json').write_text(json.dumps({'id': name, 'port': 18001}))
    calls = []
    monkeypatch.setattr(restore_drill, 'compose', lambda directory, *args, **kwargs: calls.append((directory, args)))
    return root, calls


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['restore_drill', '--env', 'production', *argv])
    return restore_drill.main()


def test_a_failed_drill_never_touches_a_real_instance_named_like_its_copy(environment, monkeypatch):
    root, calls = environment
    created = []

    def failing_drill(env, instance_id, target, *rest):
        created.append(target)
        target.mkdir()
        (target/'config.json').write_text('{}')
        raise SystemExit('pg_restore failed')

    monkeypatch.setattr(restore_drill, 'drill', failing_drill)
    with pytest.raises(SystemExit):
        run(monkeypatch, '--id', 'acme')

    # The real instance that happens to be called "<id>-drill" is intact...
    assert (root/'acme-drill'/'config.json').exists()
    assert all(directory != root/'acme-drill' for directory, _ in calls)
    # ...and the copy this run made is gone, volumes first.
    target, = created
    assert target.name.startswith('.drill-acme-') and target.parent == root
    assert not target.exists()
    assert calls == [(target, ('down', '--volumes'))]


def test_a_drill_that_created_nothing_deletes_nothing(environment, monkeypatch):
    root, calls = environment

    def refusing_drill(*args):
        raise SystemExit('refused before creating anything')

    monkeypatch.setattr(restore_drill, 'drill', refusing_drill)
    with pytest.raises(SystemExit):
        run(monkeypatch, '--id', 'acme')
    assert calls == []
    assert sorted(p.name for p in root.iterdir()) == ['acme', 'acme-drill']


@pytest.mark.parametrize('bad', ['../../x', 'Acme', 'ab', '.drill-acme', 'a/b'])
def test_an_invalid_instance_id_is_refused_before_anything_runs(environment, monkeypatch, bad):
    _, calls = environment
    monkeypatch.setattr(restore_drill, 'drill', lambda *a: pytest.fail('drill must not run'))
    with pytest.raises(SystemExit) as refused:
        run(monkeypatch, '--id', bad)
    assert refused.value.code == 2 and calls == []


def test_each_drill_gets_its_own_directory_that_no_instance_id_can_name():
    first = restore_drill.drill_target('production', 'acme')
    second = restore_drill.drill_target('production', 'acme')
    assert first != second and first.name.startswith('.') and second.name.startswith('.')


class FakeProcess:
    def __init__(self, command, stdin=None, stdout=None, text=False, **kwargs):
        import io
        self.command, self.status = command, 0
        self.stdout = io.StringIO('') if text else io.BytesIO(b'dump')
        self.stdin = None

    def wait(self, timeout=None):
        return self.status

    def poll(self):
        return self.status

    def kill(self):
        pass


class FakePsqlSession(FakeProcess):
    """psql reading queries on stdin and answering one line each."""

    def __init__(self, command, **kwargs):
        super().__init__(command, **kwargs)
        self.queries, self.pending = [], []
        session = self

        class In:
            def write(self, text):
                for statement in filter(None, (s.strip() for s in text.split(';'))):
                    session.queries.append(statement)
                    if statement.startswith('SELECT pg_export_snapshot'):
                        session.pending.append('00000003-0000001B-1\n')
                    elif statement.startswith('SELECT COUNT'):
                        session.pending.append('7\n')
                    elif statement.startswith('SELECT COALESCE'):
                        session.pending.append('12.5\n')

            def flush(self):
                pass

            def close(self):
                pass

        class Out:
            def readline(self):
                return session.pending.pop(0) if session.pending else ''

        self.stdin, self.stdout = In(), Out()


def test_the_fresh_copy_and_its_fingerprint_come_from_one_snapshot(tmp_path, monkeypatch):
    import types
    source = tmp_path/'acme'
    source.mkdir()
    (source/'config.json').write_text(json.dumps({'db_name': 'partnerhub'}))
    started = []

    def popen(command, **kwargs):
        process = FakePsqlSession(command, **kwargs) if 'psql' in command else FakeProcess(command, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(restore_drill.subprocess, 'Popen', popen)
    monkeypatch.setattr(restore_drill.subprocess, 'run', lambda *a, **k: types.SimpleNamespace(returncode=0))
    with open(tmp_path/'out', 'wb') as handle:
        original = restore_drill.consistent_dump(source, handle, 'age1recipient')

    session, dump = started
    assert session.queries[0] == 'BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert session.queries[1] == 'SELECT pg_export_snapshot()'
    assert session.queries[-1] == 'COMMIT'
    assert '--snapshot=00000003-0000001B-1' in dump.command and 'pg_dump' in dump.command
    assert set(original['rows'].values()) == {7} and set(original['money'].values()) == {12.5}


def test_an_existing_backup_is_not_compared_with_todays_live_database(tmp_path, monkeypatch):
    root = tmp_path/'instances'/'production'
    (root/'acme').mkdir(parents=True)
    (root/'acme'/'config.json').write_text(json.dumps({'name': 'Acme', 'port': 18001, 'base_url': 'https://acme.localhost:18001', 'image': 'sha256:' + 'a'*64}))
    monkeypatch.setattr(restore_drill, 'ROOT', tmp_path)
    fingerprinted = []
    monkeypatch.setattr(restore_drill, 'fingerprint', lambda directory, ask=None: fingerprinted.append(directory)
                        or {'rows': {'leads': 1}, 'money': {}})

    def fake_create(arguments, target):
        target.mkdir()
        (target/'config.json').write_text(json.dumps({'db_name': 'partnerhub'}))

    monkeypatch.setattr(restore_drill, 'create', fake_create)
    monkeypatch.setattr(restore_drill, 'database', lambda directory: None)
    monkeypatch.setattr(restore_drill, 'compose', lambda *a, **k: None)
    monkeypatch.setattr(restore_drill.subprocess, 'Popen', FakeProcess)
    target = root/'.drill-acme-0011aabb'

    report = restore_drill.drill('production', 'acme', target, source_backup='old.age', identity='key.txt')

    assert fingerprinted == [target]
    assert report['verdict'] == 'RESTORED' and report['differences'] is None and 'original' not in report
