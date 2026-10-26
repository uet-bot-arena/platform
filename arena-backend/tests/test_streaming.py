import asyncio
import unittest
from unittest.mock import Mock,patch

from arena.protocol import Fault
from arena_api.broadcast import Snapshots


class Streaming(unittest.IsolatedAsyncioTestCase):
    async def test_many_viewers_share_one_read_and_auth_refresh_is_bounded(self):
        cache=Snapshots()
        accounts=Mock()
        accounts.principal.return_value=dict(user=dict(id='person',team_id='team'))
        with patch('arena_api.broadcast.monotonic',return_value=100):
            values=await asyncio.gather(*(cache.principal(accounts,'cookie') for _ in range(100)))
            self.assertTrue(all(v==values[0] for v in values))
            self.assertEqual(accounts.principal.call_count,1)
            accounts.principal.assert_called_with('cookie',refresh=False)
        accounts.principal.side_effect=Fault('unauthorized','revoked')
        with patch('arena_api.broadcast.monotonic',return_value=113.9):
            await cache.principal(accounts,'cookie')
            self.assertEqual(accounts.principal.call_count,1)
        with patch('arena_api.broadcast.monotonic',return_value=114.1):
            with self.assertRaises(Fault):await cache.principal(accounts,'cookie')
        self.assertEqual(accounts.principal.call_count,2)
        self.assertNotIn('cookie',str(cache.items))

    async def test_quota_is_per_person_and_releases_on_finish_or_disconnect(self):
        cache=Snapshots(max_streams=2)
        async def feed():
            yield 'one'
            yield 'two'
        first=cache.limited('person',feed());second=cache.limited('person',feed())
        with self.assertRaises(Fault) as error:cache.limited('person',feed())
        self.assertEqual(error.exception.code,'rate_limited')
        other=cache.limited('other-person',feed())
        self.assertEqual(await first.__anext__(),'one')
        await first.aclose()
        replacement=cache.limited('person',feed())
        self.assertEqual([v async for v in replacement],['one','two'])
        self.assertEqual([v async for v in second],['one','two'])
        self.assertEqual([v async for v in other],['one','two'])
        self.assertEqual(cache.streams,{})

    async def test_connection_aborted_before_iteration_does_not_leak_forever(self):
        cache=Snapshots(max_streams=1)
        async def feed():yield 'one'
        with patch('arena_api.broadcast.monotonic',return_value=100):
            abandoned=cache.limited('person',feed())
        with patch('arena_api.broadcast.monotonic',return_value=166):
            replacement=cache.limited('person',feed())
        self.assertEqual([v async for v in replacement],['one'])
        await abandoned.aclose()
        self.assertEqual(cache.streams,{})
