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
    async def test_answers_visible_without_truncated_buttons(self):
        with bot.db() as con:
            bot.begin(con, 12, 'exam', [1])
            b = AsyncMock()
            await bot.show_question(b, 12, bot.session(con, 12))
            message = b.send_message.call_args.args[1]
            q = bot.BY_ID[1]
            for letter, text in zip("ABC", q['opciones']):
                self.assertIn(f"{letter}) {text}", message)
            keyboard = b.send_message.call_args.kwargs['reply_markup']
            self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ['A','B','C'])
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

class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        bot.DB_PATH = self.temp.name + '/db.sqlite3'
    def tearDown(self):
        self.temp.cleanup()
    def test_all_explanations_and_image_paths(self):
        for q in bot.QUESTIONS:
            self.assertTrue(q['regla'].strip())
            self.assertTrue(q['detalle'].strip())
            self.assertTrue(q['fuente'].startswith('https://revista.dgt.es/'))
            self.assertLess(len(bot.correction(q, q['correcta'])), 4096)
            self.assertIn('✅ CORRECTO', bot.correction(q, q['correcta']))
            self.assertIn('❌ INCORRECTO', bot.correction(q, (q['correcta']+1)%3))
            if q.get('imagen'):
                self.assertTrue((bot.Path(bot.__file__).parent / 'assets' / q['imagen']).is_file())
                self.assertTrue(q['imagen_credito'])
    async def test_image_question_is_sent_as_photo_with_answer_buttons(self):
        with bot.db() as con:
            bot.begin(con, 12, 'daily', [16])
            b = AsyncMock()
            await bot.show_question(b, 12, bot.session(con, 12))
            self.assertTrue(b.send_photo.called)
            photo_kwargs = b.send_photo.call_args.kwargs
            self.assertIn('señal', photo_kwargs['caption'])
            self.assertEqual([button.text for button in photo_kwargs['reply_markup'].inline_keyboard[0]], ['A','B','C'])
    async def _answer_first(self, mode):
        from unittest.mock import MagicMock
        with bot.db() as con:
            bot.begin(con, 12, mode, [1, 2])
            s = bot.session(con, 12)
        query = MagicMock()
        query.message.chat.type='private'; query.message.chat.id=12; query.from_user.id=12
        query.data=f'a:{s["nonce"]}:1:1'; query.answer=AsyncMock(); query.edit_message_reply_markup=AsyncMock()
        b=AsyncMock(); ctx=MagicMock(bot=b)
        await bot.answer(MagicMock(callback_query=query), ctx)
        self.assertEqual(len(b.send_message.call_args_list), 1, 'No next question until button press')
        self.assertIn('❌ INCORRECTO', b.send_message.call_args.args[1])
        self.assertIn('📌 REGLA', b.send_message.call_args.args[1])
        with bot.db() as con:
            s=bot.session(con,12)
        self.assertEqual(s['status'],'review')
        button=b.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.text, '➡️ Siguiente pregunta')
        self.assertEqual(button.callback_data, f'n:{s["nonce"]}:{s["pos"]}')
        return b,ctx,button
    async def test_exam_waits_for_next_button(self):
        from unittest.mock import MagicMock
        b,ctx,button=await self._answer_first('exam')
        query=MagicMock()
        query.message.chat.type='private';query.message.chat.id=12;query.from_user.id=12
        query.data=button.callback_data;query.answer=AsyncMock();query.edit_message_reply_markup=AsyncMock()
        await bot.next_question(MagicMock(callback_query=query),ctx)
        self.assertIn('Examen 2/2',b.send_message.call_args_list[-1].args[1])
        with bot.db() as con: self.assertEqual(bot.session(con,12)['status'],'active')
        count=len(b.send_message.call_args_list)
        await bot.next_question(MagicMock(callback_query=query),ctx)
        self.assertEqual(len(b.send_message.call_args_list),count)
        query.answer.assert_awaited()
    async def test_daily_waits_for_next_button(self):
        b,ctx,button=await self._answer_first('daily')
        self.assertEqual(button.text,'➡️ Siguiente pregunta')
    async def test_pause_resume_while_reading_correction(self):
        from unittest.mock import MagicMock
        b,ctx,button=await self._answer_first('exam')
        u=MagicMock(); u.effective_chat.id=12;u.message.reply_text=AsyncMock()
        await bot.pause(u,ctx)
        with bot.db() as con: self.assertEqual(bot.session(con,12)['status'],'paused_review')
        await bot.resume(u,ctx)
        with bot.db() as con: self.assertEqual(bot.session(con,12)['status'],'review')
        self.assertEqual(len(b.send_message.call_args_list),1)
        self.assertIn('última corrección',u.message.reply_text.call_args.args[0])
    async def test_final_answer_waits_for_result_button(self):
        from unittest.mock import MagicMock
        with bot.db() as con:
            bot.begin(con,12,'daily',[1]);s=bot.session(con,12)
        q=MagicMock();q.message.chat.type='private';q.message.chat.id=12;q.from_user.id=12
        q.data=f'a:{s["nonce"]}:1:0';q.answer=AsyncMock();q.edit_message_reply_markup=AsyncMock()
        b=AsyncMock();ctx=MagicMock(bot=b)
        await bot.answer(MagicMock(callback_query=q),ctx)
        self.assertEqual(len(b.send_message.call_args_list),1)
        button=b.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.text,'📊 Ver resultado')
        q.data=button.callback_data
        await bot.next_question(MagicMock(callback_query=q),ctx)
        self.assertIn('Práctica: 1/1',b.send_message.call_args_list[-1].args[1])
        with bot.db() as con: self.assertEqual(bot.session(con,12)['status'],'finished')
