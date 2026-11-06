"""Run test worker threads in the database context of the test that starts them.

A new thread starts with no database context (api/postgres.py), and row-level
security then shows it nothing -- exactly right for production, where every
request sets its own. Race tests stand in for parallel requests, so each
worker carries a copy of the starting test's context.
"""
import contextvars


def carry_context(fn):
    snapshot = contextvars.copy_context()

    def run(*args, **kwargs):
        return snapshot.copy().run(fn, *args, **kwargs)

    return run
