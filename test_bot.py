"""Pruebas offline: python -m unittest -v test_bot.py"""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock
from datetime import timedelta

with tempfile.TemporaryDirectory() as _tmp:
    os.environ['DB_PATH'] = _tmp + '/dgt.sqlite3'
    import bot


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        bot.DB_PATH = self.temp.name + '/db.sqlite3'
    def tearDown(self):
        self.temp.cleanup()
    def test_question_bank(self):
        self.assertEqual(len(bot.QUESTIONS),30)
        self.assertEqual(len(bot.BY_ID),30)
        for q in bot.QUESTIONS:
            self.assertEqual(len(q['opciones']),3)
            self.assertIn(q['correcta'],(0,1,2))
    def test_exam_session_and_pause_clock(self):
        with bot.db() as con:
            bot.begin(con, 12,'exam',list(range(1,31)))
            s=bot.session(con,12)
            self.assertEqual(len(__import__('json').loads(s['ids'])),30)
            self.assertTrue(1790 <= bot.remaining_seconds(s) <= 1800)
    async def test_result_pass(self):
        with bot.db() as con:
            bot.begin(con,12,'exam',list(range(1,31)))
            s=bot.session(con,12)
            answers=[{'id':i,'choice':bot.BY_ID[i]['correcta']} for i in range(1,31)]
            con.execute('UPDATE sessions SET answers=?,pos=30 WHERE chat_id=12',(__import__('json').dumps(answers),))
            b=AsyncMock()
            await bot.finish(b,con,12,bot.session(con,12))
            self.assertIn('APTO',b.send_message.call_args_list[0].args[1])
            self.assertEqual(bot.session(con,12)['status'],'finished')
    async def test_result_unanswered_fails(self):
        with bot.db() as con:
            bot.begin(con,12,'exam',list(range(1,31)))
            b=AsyncMock()
            await bot.finish(b,con,12,bot.session(con,12),timed_out=True)
            self.assertIn('NO APTO',b.send_message.call_args_list[0].args[1])

if __name__=='__main__': unittest.main()
