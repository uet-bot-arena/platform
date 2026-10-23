"""Windows pipe scheduling, with deterministic fake I/O and no native bot."""
import os
from pathlib import Path
import queue
import threading
import time
import unittest
from unittest.mock import patch,Mock

if os.name=='nt':
    from arena.supervisor_windows import WindowsBot
from arena.protocol import Fault


@unittest.skipUnless(os.name=='nt','Windows pipe supervisor')
class WindowsPipe(unittest.TestCase):
    def bot(self,delivered=True,trailing=False,error=False):
        bot=WindowsBot.__new__(WindowsBot)
        bot.buffer=bytearray();bot.out=queue.Queue();bot.log_bytes=0;bot.remaining_ns=None
        bot.profile={};bot.work=Path('.');bot.container=Mock(folder=Path('.'))
        bot.resume=Mock();bot.freeze=Mock();bot.metrics=Mock(return_value=dict(cpu_usage_usec=0,memory_peak_bytes=0))
        def writer(frame,done):
            bot.out.put((time.monotonic_ns(),b'{}\n'+(b'{}\n' if trailing else b'')))
            time.sleep(.02)  # Reply precedes the trusted WriteFile thread's return.
            if error:bot.writer_error=OSError('synthetic broken pipe')
            if delivered:done.set()
        bot.write_frame=writer
        return bot

    def exchange(self,bot,budget=1000):
        with patch('arena.supervisor_windows.emit'),patch('arena.supervisor_windows.check_work_quota'),patch('arena.supervisor_windows.response',return_value=dict(payload='ok')):
            return bot.exchange(dict(payload={}),budget,'ready')

    def test_fast_reply_waits_for_trusted_writer_completion(self):
        bot=self.bot()
        result=self.exchange(bot)
        self.assertEqual(result['message']['payload'],'ok')
        self.assertLess(result['wall_ms'],20,'clock uses response arrival, not writer-thread wakeup')
        self.assertTrue(bot.freeze.called)

    def test_duplicate_output_undelivered_request_and_broken_pipe_still_fail(self):
        for options,code in ((dict(trailing=True),'protocol_error'),(dict(delivered=False),'protocol_error'),(dict(error=True),'crash')):
            with self.subTest(options=options):
                with self.assertRaises(Fault) as result:self.exchange(self.bot(**options),100)
                self.assertEqual(result.exception.code,code)
