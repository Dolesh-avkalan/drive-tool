"""Edge-case tests for drive_tool.service_policy (dt5-cases-1).

Pure unittest, stdlib only, synthetic tokens and roots, same setup
helpers as tests/test_service_policy.py.
"""

from __future__ import annotations

import hashlib
import unittest

from drive_tool.service_policy import (
    MAX_TRANSFER_BYTES,
    PolicyDeniedError,
    SourceGrant,
    AgentPolicy,
    PolicyTable,
)

def make_token(i: int) -> str:
    """Distinct 32-char visible-ASCII token for index ``i``."""
    s = 'tok{:029d}'.format(i)
    assert len(s) == 32
    return s

def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode('ascii')).digest()

def make_grant(alias: str = 'main',
               root: str = '/srv/data',
               ops: frozenset = frozenset({'read', 'find'}),
               r: int = MAX_TRANSFER_BYTES,
               w: int = MAX_TRANSFER_BYTES) -> SourceGrant:
    return SourceGrant(alias=alias, root=root, operations=ops,
                       max_read_bytes=r, max_write_bytes=w)

def make_agent(agent_id: str = 'a',
               token: str | None = None,
               grants: tuple | None = None) -> AgentPolicy:
    if token is None:
        token = make_token(0)
    if grants is None:
        grants = (make_grant(),)
    return AgentPolicy(agent_id=agent_id, token_sha256=digest(token),
                       grants=grants)

class RootTrailingSlashTests(unittest.TestCase):
    """CASE 1: root with a trailing slash is rejected."""

    def test_trailing_slash_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            make_grant(root='/data/')
        self.assertEqual(str(ctx.exception),
                         'root must not contain empty components')

    def test_plain_root_still_accepted(self):
        self.assertEqual(make_grant(root='/data').root, '/data')

class CapBoolTests(unittest.TestCase):
    """CASE 2: bool is not accepted as an int cap."""

    def test_true_max_read_bytes_raises_type_error(self):
        with self.assertRaises(TypeError) as ctx:
            make_grant(r=True)
        self.assertEqual(str(ctx.exception),
                         'max_read_bytes must be int')

    def test_true_max_write_bytes_raises_type_error(self):
        with self.assertRaises(TypeError) as ctx:
            make_grant(w=True)
        self.assertEqual(str(ctx.exception),
                         'max_write_bytes must be int')

class TooManyAgentsTests(unittest.TestCase):
    """CASE 3: PolicyTable rejects more than 32 agents."""

    def test_33_agents_raises_value_error(self):
        agents = tuple(make_agent(agent_id=f'a{i}', token=make_token(i))
                       for i in range(33))
        with self.assertRaises(ValueError) as ctx:
            PolicyTable(agents)
        self.assertEqual(str(ctx.exception),
                         'agents must contain at most 32 entries')

    def test_32_agents_accepted(self):
        agents = tuple(make_agent(agent_id=f'a{i}', token=make_token(i))
                       for i in range(32))
        table = PolicyTable(agents)
        self.assertIs(table.authenticate(make_token(31)), agents[31])

class ShortTokenTests(unittest.TestCase):
    """CASE 4: a 31-char token is denied at authentication."""

    def setUp(self):
        self.tok = make_token(0)
        self.agent = make_agent(agent_id='a', token=self.tok)
        self.table = PolicyTable((self.agent,))

    def test_31_char_token_denied(self):
        short = self.tok[:31]
        self.assertEqual(len(short), 31)
        with self.assertRaises(PolicyDeniedError) as ctx:
            self.table.authenticate(short)
        self.assertEqual(str(ctx.exception), 'access denied')

    def test_full_token_still_accepted(self):
        self.assertIs(self.table.authenticate(self.tok), self.agent)

class AuthorizeOperationNotPermittedTests(unittest.TestCase):
    """CASE 5: authorize denies an operation absent from the grant."""

    def setUp(self):
        self.tok = make_token(0)
        self.grant = make_grant(alias='src', ops=frozenset({'read'}))
        self.agent = AgentPolicy(agent_id='a', token_sha256=digest(self.tok),
                                 grants=(self.grant,))
        self.table = PolicyTable((self.agent,))

    def test_save_not_permitted_denied(self):
        with self.assertRaises(PolicyDeniedError) as ctx:
            self.table.authorize('a', 'src', 'save')
        self.assertEqual(str(ctx.exception), 'access denied')

    def test_list_not_permitted_denied(self):
        with self.assertRaises(PolicyDeniedError) as ctx:
            self.table.authorize('a', 'src', 'list')
        self.assertEqual(str(ctx.exception), 'access denied')

    def test_read_permitted_returns_exact_grant(self):
        self.assertIs(self.table.authorize('a', 'src', 'read'), self.grant)

if __name__ == '__main__':  # pragma: no cover
    unittest.main()
