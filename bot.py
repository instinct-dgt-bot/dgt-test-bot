#!/usr/bin/env python3
"""Práctica con preguntas publicadas en la Revista DGT; no es un examen oficial."""
import json
import hashlib
import logging
import os
import random
import sqlite3
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, Defaults

TZ = ZoneInfo("Europe/Madrid")
QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
BY_ID = {q["id"]: q for q in QUESTIONS}
BANK_VERSION = hashlib.sha256(json.dumps(QUESTIONS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "dgt_bot.sqlite3"))
DAILY_COUNT = int(os.getenv("DAILY_COUNT", "3"))
assert 3 <= DAILY_COUNT <= 5, "DAILY_COUNT debe estar entre 3 y 5"
logging.basicConfig(level=logging.INFO)
# httpx INFO logs include Bot API URLs, which embed the Telegram secret token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, last_daily TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
      chat_id INTEGER PRIMARY KEY, mode TEXT NOT NULL, ids TEXT NOT NULL,
      pos INTEGER NOT NULL, answers TEXT NOT NULL, status TEXT NOT NULL,
      deadline TEXT, remaining INTEGER, nonce INTEGER NOT NULL DEFAULT 0)""")
    con.execute("CREATE TABLE IF NOT EXISTS history (chat_id INTEGER, played_at TEXT, mode TEXT, question_id INTEGER, choice INTEGER, correct INTEGER, topic TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    version = con.execute("SELECT value FROM metadata WHERE key='bank_version'").fetchone()
    if not version or version["value"] != BANK_VERSION:
        # Existing sessions refer to old IDs. Never grade them using the replacement bank.
        con.execute("DELETE FROM sessions")
        con.execute("DELETE FROM history")
        con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('bank_version',?)", (BANK_VERSION,))
    con.commit()
    return con


def now():
    return datetime.now(TZ)


def session(con, chat_id):
    row = con.execute("SELECT * FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


def remaining_seconds(s):
    return max(0, int((datetime.fromisoformat(s["deadline"]) - now()).total_seconds())) if s["deadline"] else int(s["remaining"] or 0)


def select_questions(con, chat_id, n):
    # Dar más peso a temas fallados en el historial, sin duplicados en el test.
    rows = con.execute("SELECT topic, COUNT(*) AS n FROM history WHERE chat_id=? AND correct=0 GROUP BY topic", (chat_id,)).fetchall()
    mistakes = Counter({r["topic"]: r["n"] for r in rows})
    pool = QUESTIONS[:]
    random.shuffle(pool)
    pool.sort(key=lambda q: mistakes[q["tema"]], reverse=True)
    return [q["id"] for q in pool[:n]]


def begin(con, chat_id, mode, ids):
    deadline = (now() + timedelta(minutes=30)).isoformat() if mode == "exam" else None
    con.execute("""INSERT INTO sessions(chat_id,mode,ids,pos,answers,status,deadline,remaining,nonce)
      VALUES(?,?,?,?,?,'active',?,?,0) ON CONFLICT(chat_id) DO UPDATE SET
      mode=excluded.mode,ids=excluded.ids,pos=0,answers='[]',status='active',
      deadline=excluded.deadline,remaining=excluded.remaining,nonce=nonce+1""",
      (chat_id, mode, json.dumps(ids), 0, "[]", deadline, None))
    con.commit()


def correction(q, choice):
    letter = chr(65 + q["correcta"])
    selected = chr(65 + choice)
    verdict = f"✅ CORRECTO · Elegiste {selected}" if choice == q["correcta"] else f"❌ INCORRECTO · Elegiste {selected}"
    return (f"{verdict}\n"
            f"🎯 Respuesta: {letter}) {q['opciones'][q['correcta']]}\n\n"
            f"📌 REGLA\n{q['regla']}\n\n"
            f"💡 POR QUÉ\n{q['detalle']}\n\n"
            f"🔗 Fuente DGT: {q['fuente']}")


async def show_question(bot, chat_id, s):
    ids = json.loads(s["ids"])
    if s["pos"] >= len(ids):
        return
    q = BY_ID[ids[s["pos"]]]
    # Telegram truncates long inline button labels. Put the complete answers in the
    # message body; keep the keyboard labels to one letter each.
    options = InlineKeyboardMarkup([[InlineKeyboardButton(letter, callback_data=f"a:{s['nonce']}:{q['id']}:{i}")
                                     for i, letter in enumerate("ABC")]])
    heading = f"{'Examen' if s['mode']=='exam' else 'Práctica'} {s['pos']+1}/{len(ids)} · {q['tema']}"
    if s["mode"] == "exam":
        heading += f" · {max(0, (remaining_seconds(s)+59)//60)} min restantes"
    answers = "\n".join(f"{letter}) {text}" for letter, text in zip("ABC", q["opciones"]))
    body = f"📝 {heading}\n\n{q['pregunta']}\n\n{answers}"
    image = q.get("imagen")
    if image:
        path = Path(__file__).parent / "assets" / image
        if not path.is_file():
            raise FileNotFoundError(f"Imagen necesaria ausente para pregunta {q['id']}: {image}")
        if len(body) <= 1024:
            with path.open("rb") as photo:
                await bot.send_photo(chat_id, photo=photo, caption=body, reply_markup=options)
            return
        with path.open("rb") as photo:
            await bot.send_photo(chat_id, photo=photo, caption=f"Imagen de la pregunta {s['pos']+1}: {q['imagen_credito']}")
    await bot.send_message(chat_id, body, reply_markup=options)


async def finish(bot, con, chat_id, s, timed_out=False):
    ids, answers = json.loads(s["ids"]), json.loads(s["answers"])
    keyed = {a["id"]: a["choice"] for a in answers}
    correct = sum(keyed.get(i) == BY_ID[i]["correcta"] for i in ids)
    errors = len(answers) - correct
    unanswered = len(ids) - len(answers)
    per_topic = {}
    for i in ids:
        q = BY_ID[i]
        stats = per_topic.setdefault(q["tema"], [0, 0])
        stats[1] += 1
        stats[0] += (keyed.get(i) == q["correcta"])
    result = f"{'Tiempo agotado. ' if timed_out else ''}{'Examen' if s['mode']=='exam' else 'Práctica'}: {correct}/{len(ids)}. {errors} fallos, {unanswered} sin responder."
    if s["mode"] == "exam":
        result += f"\n{'APTO' if (not timed_out and errors + unanswered <= 3) else 'NO APTO'} (máximo 3 fallos; sin responder cuenta como fallo)."
    result += "\n\nPor tema: " + "; ".join(f"{k} {v[0]}/{v[1]}" for k,v in sorted(per_topic.items()))
    week_start = (now().date() - timedelta(days=6)).isoformat()
    if s["mode"] == "daily":
        days = con.execute("SELECT DISTINCT substr(played_at,1,10) AS day FROM history WHERE chat_id=? AND mode='daily' AND played_at>=? ORDER BY day", (chat_id, week_start)).fetchall()
        past_days = {r["day"] for r in days}
        past_days.add(now().date().isoformat())
        streak = 0
        day = now().date()
        while day.isoformat() in past_days:
            streak += 1
            day -= timedelta(days=1)
        historical = con.execute("SELECT COUNT(*) AS total, SUM(correct) AS hits FROM history WHERE chat_id=? AND mode='daily' AND played_at>=?", (chat_id, week_start)).fetchone()
        result += f"\n\nÚltimos 7 días: {(historical['hits'] or 0) + correct}/{historical['total'] + len(answers)} respuestas correctas; racha de {streak} días."
    for a in answers:
        q = BY_ID[a["id"]]
        con.execute("INSERT INTO history(chat_id,played_at,mode,question_id,choice,correct,topic) VALUES(?,?,?,?,?,?,?)",
                    (chat_id, now().isoformat(), s["mode"], a["id"], a["choice"], int(a["choice"] == q["correcta"]), q["tema"]))
    con.commit()
    # Bot API limita los mensajes a 4096 caracteres; separar por párrafos.
    parts = result.split("\n\n")
    batch = ""
    for part in parts:
        candidate = f"{batch}\n\n{part}" if batch else part
        if len(candidate) > 3800:
            if batch:
                await bot.send_message(chat_id, batch)
            batch = part
        else:
            batch = candidate
    if batch:
        await bot.send_message(chat_id, batch)
    con.execute("UPDATE sessions SET status='finished',deadline=NULL WHERE chat_id=?", (chat_id,))
    con.commit()


async def deadline_check(bot, con, chat_id, s):
    if s and s["status"] in ("active", "review") and s["mode"] == "exam" and remaining_seconds(s) <= 0:
        await finish(bot, con, chat_id, s, timed_out=True)
        return True
    return False


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Úsame en un chat privado.")
        return
    chat_id = update.effective_chat.id
    with db() as con:
        con.execute("INSERT INTO users(chat_id) VALUES(?) ON CONFLICT(chat_id) DO UPDATE SET enabled=1", (chat_id,))
    await update.message.reply_text("Hola. Soy un prototipo de práctica, no un test oficial DGT. Cada día te mando tres preguntas a las 9:00 (hora peninsular). /diario para practicar ahora; /examen para 30 preguntas en 30 minutos. /pausar, /seguir, /abandonar, /repetir, /baja.")


async def begin_command(update, ctx, mode):
    if update.effective_chat.type != "private":
        return
    chat_id = update.effective_chat.id
    with db() as con:
        s = session(con, chat_id)
        if s and s["status"] in ("active", "paused", "review", "paused_review"):
            if await deadline_check(ctx.bot, con, chat_id, s):
                s = session(con, chat_id)
            else:
                await update.message.reply_text("Ya tienes un test en curso. Usa /seguir o /abandonar antes de iniciar otro.")
                return
        if mode == "daily":
            row = con.execute("SELECT last_daily FROM users WHERE chat_id=?", (chat_id,)).fetchone()
            if not row:
                await update.message.reply_text("Pulsa /start primero para activar las preguntas diarias.")
                return
            if row[0] == now().date().isoformat():
                await update.message.reply_text("Ya has hecho las preguntas de hoy. Mañana tendrás otras. Puedes pedir /examen o /repetir.")
                return
            con.execute("UPDATE users SET last_daily=? WHERE chat_id=?", (now().date().isoformat(),chat_id))
        ids = select_questions(con, chat_id, 30 if mode == "exam" else DAILY_COUNT)
        begin(con, chat_id, mode, ids)
        s = session(con, chat_id)
    await show_question(ctx.bot, chat_id, s)


async def daily(update, ctx):
    await begin_command(update, ctx, "daily")


async def exam(update, ctx):
    await begin_command(update, ctx, "exam")


async def answer(update, ctx):
    query = update.callback_query
    if query.message.chat.type != "private" or query.from_user.id != query.message.chat.id:
        await query.answer("Esta pregunta no es tuya.", show_alert=True)
        return
    try:
        _, nonce, qid, choice = query.data.split(":")
        nonce, qid, choice = int(nonce), int(qid), int(choice)
    except (ValueError, AttributeError):
        await query.answer("Respuesta no válida.", show_alert=True)
        return
    chat_id = query.message.chat.id
    with db() as con:
        s = session(con, chat_id)
        if not s or s["status"] != "active":
            await query.answer("Esta pregunta ya no está activa.", show_alert=True)
            return
        if await deadline_check(ctx.bot, con, chat_id, s):
            await query.answer("Se terminó el tiempo.", show_alert=True)
            return
        ids = json.loads(s["ids"])
        if s["nonce"] != nonce or s["pos"] >= len(ids) or ids[s["pos"]] != qid or choice not in (0,1,2):
            await query.answer("Esta pregunta ya no está activa.", show_alert=True)
            return
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        q = BY_ID[qid]
        answers = json.loads(s["answers"])
        answers.append({"id": qid, "choice": choice})
        con.execute("UPDATE sessions SET pos=pos+1,answers=?,status='review',nonce=nonce+1 WHERE chat_id=?", (json.dumps(answers), chat_id))
        con.commit()
        s = session(con, chat_id)
        label = "📊 Ver resultado" if s["pos"] == len(ids) else "➡️ Siguiente pregunta"
        button = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"n:{s['nonce']}:{s['pos']}")]])
        await ctx.bot.send_message(chat_id, correction(q, choice), reply_markup=button)


async def next_question(update, ctx):
    query = update.callback_query
    if query.message.chat.type != "private" or query.from_user.id != query.message.chat.id:
        await query.answer("Esta pregunta no es tuya.", show_alert=True)
        return
    try:
        _, nonce, pos = query.data.split(":")
        nonce, pos = int(nonce), int(pos)
    except (ValueError, AttributeError):
        await query.answer("Botón no válido.", show_alert=True)
        return
    chat_id = query.message.chat.id
    with db() as con:
        s = session(con, chat_id)
        if not s or s["status"] != "review":
            await query.answer("Reanuda el test o utiliza el botón de la corrección actual.", show_alert=True)
            return
        if await deadline_check(ctx.bot, con, chat_id, s):
            await query.answer("Se terminó el tiempo.", show_alert=True)
            return
        if s["nonce"] != nonce or s["pos"] != pos:
            await query.answer("Esta corrección ya no está activa.", show_alert=True)
            return
        await query.answer()
        # Persist the transition before sending, so double-taps cannot advance twice.
        con.execute("UPDATE sessions SET status='active',nonce=nonce+1 WHERE chat_id=?", (chat_id,))
        con.commit()
        s = session(con, chat_id)
        await query.edit_message_reply_markup(reply_markup=None)
        if s["pos"] == len(json.loads(s["ids"])):
            await finish(ctx.bot, con, chat_id, s)
        else:
            await show_question(ctx.bot, chat_id, s)


async def pause(update, ctx):
    with db() as con:
        s = session(con, update.effective_chat.id)
        if not s or s["status"] not in ("active", "review"):
            await update.message.reply_text("No hay un test activo.")
            return
        if await deadline_check(ctx.bot, con, update.effective_chat.id, s):
            return
        remain = remaining_seconds(s) if s["mode"] == "exam" else None
        paused_status = "paused_review" if s["status"] == "review" else "paused"
        con.execute("UPDATE sessions SET status=?,remaining=?,deadline=NULL,nonce=nonce+? WHERE chat_id=?",
                    (paused_status, remain, 0 if paused_status == "paused_review" else 1, update.effective_chat.id))
    await update.message.reply_text("Pausado. /seguir para reanudar. El cronómetro queda detenido.")


async def resume(update, ctx):
    with db() as con:
        s = session(con, update.effective_chat.id)
        if not s or s["status"] not in ("paused", "paused_review"):
            await update.message.reply_text("No hay un test pausado.")
            return
        deadline = (now()+timedelta(seconds=s["remaining"])).isoformat() if s["mode"] == "exam" else None
        was_review = s["status"] == "paused_review"
        con.execute("UPDATE sessions SET status=?,deadline=?,remaining=NULL,nonce=nonce+? WHERE chat_id=?",
                    ("review" if was_review else "active", deadline, 0 if was_review else 1, update.effective_chat.id))
        con.commit()
        s = session(con, update.effective_chat.id)
    if was_review:
        await update.message.reply_text("Reanudado. Pulsa el botón bajo la última corrección cuando quieras continuar.")
    else:
        await show_question(ctx.bot, update.effective_chat.id, s)


async def abandon(update, ctx):
    with db() as con:
        s = session(con, update.effective_chat.id)
        if not s or s["status"] not in ("active","paused","review","paused_review"):
            await update.message.reply_text("No hay un test en curso.")
            return
        con.execute("UPDATE sessions SET status='abandoned',deadline=NULL,nonce=nonce+1 WHERE chat_id=?", (update.effective_chat.id,))
    await update.message.reply_text("Test abandonado. Puedes pedir /examen o /repetir.")


async def repeat(update, ctx):
    chat_id = update.effective_chat.id
    with db() as con:
        s = session(con, chat_id)
        if not s:
            await update.message.reply_text("Aún no tienes fallos para repetir.")
            return
        if s["status"] in ("active","paused","review","paused_review"):
            if await deadline_check(ctx.bot, con, chat_id, s):
                s = session(con, chat_id)
            else:
                await update.message.reply_text("Termina o abandona el test actual primero.")
                return
        ids = [a["id"] for a in json.loads(s["answers"]) if a["choice"] != BY_ID[a["id"]]["correcta"]]
        if not ids:
            await update.message.reply_text("El último test no tiene errores respondidos.")
            return
        begin(con, chat_id, "repeat", ids)
        s = session(con, chat_id)
    await show_question(ctx.bot, chat_id, s)


async def optout(update, ctx):
    with db() as con:
        con.execute("UPDATE users SET enabled=0 WHERE chat_id=?", (update.effective_chat.id,))
    await update.message.reply_text("Avisos diarios desactivados. /start para volver a activarlos.")


async def daily_job(ctx):
    today = now().date().isoformat()
    with db() as con:
        users = con.execute("SELECT chat_id FROM users WHERE enabled=1 AND (last_daily IS NULL OR last_daily!=?)",(today,)).fetchall()
        for row in users:
            chat_id = row["chat_id"]
            s = session(con, chat_id)
            if s and s["status"] in ("active","paused","review","paused_review"):
                if s["status"] in ("active", "review") and await deadline_check(ctx.bot, con, chat_id, s):
                    pass
                else:
                    continue
            try:
                ids = select_questions(con, chat_id, DAILY_COUNT)
                begin(con, chat_id, "daily", ids)
                await show_question(ctx.bot, chat_id, session(con, chat_id))
                con.execute("UPDATE users SET last_daily=? WHERE chat_id=?",(today,chat_id))
                con.commit()
            except Exception:
                log.exception("No se pudo entregar práctica a chat %s", chat_id)


async def expiry_job(ctx):
    with db() as con:
        rows = con.execute("SELECT chat_id FROM sessions WHERE status IN ('active','review') AND mode='exam' AND deadline IS NOT NULL AND deadline<=?", (now().isoformat(),)).fetchall()
        for row in rows:
            await deadline_check(ctx.bot, con, row["chat_id"], session(con, row["chat_id"]))


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token == "PEGA_AQUI_EL_TOKEN_DE_BOTFATHER":
        raise SystemExit("Define TELEGRAM_BOT_TOKEN como variable de entorno.")
    db().close()
    app = Application.builder().token(token).defaults(Defaults(tzinfo=TZ)).build()
    for name, callback in [("start",start),("diario",daily),("examen",exam),("pausar",pause),("seguir",resume),("abandonar",abandon),("repetir",repeat),("baja",optout)]:
        app.add_handler(CommandHandler(name,callback))
    app.add_handler(CallbackQueryHandler(answer, pattern=r"^a:\d+:\d+:[012]$"))
    app.add_handler(CallbackQueryHandler(next_question, pattern=r"^n:\d+:\d+$"))
    app.job_queue.run_daily(daily_job, time=time(int(os.getenv("DAILY_HOUR","9")), int(os.getenv("DAILY_MINUTE","0")), tzinfo=TZ), name="daily")
    app.job_queue.run_repeating(expiry_job, interval=15, first=5, name="expiry")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
