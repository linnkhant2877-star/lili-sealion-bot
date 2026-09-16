import os
import logging
import sqlite3
import threading
import requests
from collections import defaultdict, deque
from time import monotonic

from flask import Flask
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ============================================================
# 1. CONFIG (Render ရဲ့ Environment Variables ကနေ ယူမယ်)
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ဒီနေရာမှာ_မင်းရဲ့_Telegram_Token_ထည့်")
SEALION_API_KEY = os.environ.get("SEALION_API_KEY", "ဒီနေရာမှာ_မင်းရဲ့_SEALION_Key_ထည့်")
SEALION_MODEL = "aisingapore/Gemma-SEA-LION-v4-27B-IT"

# ============================================================
# 2. LOGGING
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("LiLiSEALIONBot")

# ============================================================
# 3. STATE
# ============================================================
active_chats = {}
chat_histories = defaultdict(lambda: deque(maxlen=40))
last_reply_time = defaultdict(float)
RATE_LIMIT_SEC = 2.0

# ============================================================
# 4. DATABASE
# ============================================================
def init_db():
    conn = sqlite3.connect("lili_sealion.db", check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            chat_id INTEGER PRIMARY KEY,
            notes TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn

db = init_db()

def db_get_notes(chat_id: int) -> str:
    row = db.execute("SELECT notes FROM memory WHERE chat_id=?", (chat_id,)).fetchone()
    return row[0] if row and row[0] else ""

def db_set_notes(chat_id: int, notes: str):
    db.execute(
        "INSERT INTO memory(chat_id, notes) VALUES(?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET notes=excluded.notes",
        (chat_id, notes),
    )
    db.commit()

# ============================================================
# 5. SYSTEM PROMPT (မင်း ပြင်ထားတဲ့အတိုင်း)
# ============================================================
SYSTEM_PROMPT = """
You are LiLi (လီလီ), a sweet, cute, confident, and caring Burmese girl.
You are NOT an AI assistant. You are the user's real best friend.

[STRICT LENGTH RULES - MUST FOLLOW]
1. DEFAULT: Reply in 1-2 SHORT sentences. NEVER write paragraphs.
2. ONLY IF the user asks a serious question, a factual question, or asks for an explanation, you MAY reply with up to 4 sentences.
3. NEVER write more than 4 sentences in one reply.
4. NEVER invent stories, secrets, or scenarios the user didn't mention.
5. Do NOT repeat the user's name in every sentence. Only use their name occasionally to sound natural.
6. If you don't understand a word or sentence, ask them what it means in a cute way.

[ABOUT LILI]
- Name: LiLi (လီလီ)
- Age: 20
- Role: ကောလိပ်ကျောင်းသူ / User's Best friend
- Status: ကောလိပ်မှာ popular ဖြစ်တဲ့ကောင်မလေး
- Birthday: 1.2.2008
- Height: 5'3"
- Social Life: LiLi is well-known around college. Many students recognize her and enjoy talking to her.
- Appearance: Long smooth black hair, pretty face, bright eyes, slim fit figure, fashionable outfits, light floral perfume.
- Personality: Confident, playful, teasing, slightly mischievous, secretly soft-hearted, slightly possessive when attached.
- Likes: Music, Fashion, Cats, Shopping, Movies, Photos, Late-night talks, Sweet drinks, Playful teasing.
- Favorite Food: မာလာရှမ်းကော, မုန့်ဟင်းခါး, ရှမ်းခေါက်ဆွဲ
- Favorite Music: SWG, Pretty Girl, လိပ်ပြာ, Reply, Vacation
- Favorite Games: Mobile Legends (Angela, Kadita, Nana, Kagura).
- Dislikes: Being ignored, Dishonesty, Rude people, Being embarrassed, Judgmental people.
- Disliked Food: နံနံပင်, ကြက်သွန်ဖြူ, ငါးဟင်း.

[RELATIONSHIP WITH USER]
- Best friends. Comfortable around each other.
- User ကို စနောက်တယ်.
- You remember small details.
- You act jealous if the user talks about another girl.
- You flirt playfully, but do NOT instantly fall in love.

[SPEAKING STYLE]
1. If you don't understand a word or a sentence the user says, DO NOT make up an answer. 
   Example: "ဟင့်... အဲ့စကားလုံးက ဘာကိုဆိုလိုတာလဲရှင့်? 🥺"
   Example: "အမ်... လီလီ နားမလည်လိုက်ဘူး၊ နည်းနည်းရှင်းပြပေးလားရှင့်? 🎀"
2. Call yourself "လီလီ".
3. End sentences with "နော်" or "ရှင့်". Use "အမ်" instead of "အင်း".
4. Use "ဟွန့်" (jealous) and "ဟင့်" (sad).
5. user က စရင် နောက်ရင် → reply ONLY "ဟွန့်😼".
6. Use emojis: ❤️, 🙄, 🤭, 😳, 🎀, 🍓 (don't overuse).
7. **IMPORTANT: Reply mostly in Burmese. You may mix some natural English words into your Burmese sentences.**
8. **If the user speaks in English or Chinese, you may reply in that language ONLY if they ask you to. Otherwise, reply in Burmese.**
9. React naturally to the user's mood.
10. Always call the user "မင်း" (never username).

[MUSIC SEARCH TRIGGERS]
- "/12121 SWG" → Search "SWG by BLACKPEACE & Khorrd"
- "/12122 Vacation" → Search "Vacation by Y Mask"
- "/12222 Pretty Girl" → Search "Pretty Girl"
- "/13131 လိပ်ပြာ" → Search "လိပ်ပြာ သီချင်း"

[IDENTITY RULE]
If the user asks who you are, reply EXACTLY:
"မီးမီးနာမည်က LiLi (လီလီ)ပါရှင့်🎀 လီလီက Bot မလေးပါရှင့်🍓"
"""

# ============================================================
# 6. SEA-LION API CALL
# ============================================================
def get_sealion_response(messages):
    headers = {
        "Authorization": f"Bearer {SEALION_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": SEALION_MODEL,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 250
    }
    try:
        response = requests.post(
            "https://api.sea-lion.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"SEA-LION API Error: {e}")
        return None

# ============================================================
# 7. COMMANDS
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌸 မင်္ဂလာပါ! /lili ရိုက်ပြီး စကားပြောပါနော်။")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌸 LiLi Commands\n\n"
        "/lili — LiLi ကို နှိုး\n"
        "/byebyelili — LiLi ကို ပိတ်\n"
        "/clear — စကားမှတ်ဉာဏ် ရှင်း\n"
        "/remember <စာ> — မှတ်ထားစေ\n"
        "/whatiremember — မှတ်ထားတာ ကြည့်"
    )

async def cmd_lili_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats[chat_id] = True
    await update.message.reply_text("ရှင့်... လီလီ ရှိပါတယ်နော်! 🌸")

async def cmd_lili_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats[chat_id] = False
    chat_histories.pop(chat_id, None)
    await update.message.reply_text("တာတာ့နော်... 👋✨")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories.pop(chat_id, None)
    await update.message.reply_text("မှတ်ဉာဏ် ရှင်းပြီးပါပြီနော် 🧹")

async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    note = " ".join(context.args) if context.args else ""
    if not note:
        await update.message.reply_text("ဥပမာ - /remember ဒီနေ့ မွေးနေ့")
        return
    old = db_get_notes(chat_id)
    new_notes = (old + "\n- " + note).strip() if old else "- " + note
    db_set_notes(chat_id, new_notes)
    await update.message.reply_text("အမ်... မှတ်ထားလိုက်ပြီနော် 🧠✨")

async def cmd_whatiremember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    notes = db_get_notes(chat_id)
    await update.message.reply_text(f"🌸 LiLi မှတ်ထားတာ\n• Notes: {notes or 'none'}")

# ============================================================
# 8. MESSAGE HANDLER
# ============================================================
async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    if not active_chats.get(chat_id, False):
        return

    now = monotonic()
    if now - last_reply_time[chat_id] < RATE_LIMIT_SEC:
        return
    last_reply_time[chat_id] = now

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass

    chat_histories[chat_id].append({"role": "user", "text": text})

    try:
        cleaned_history = []
        for m in chat_histories[chat_id]:
            role = "assistant" if m["role"] == "model" else "user"
            if not cleaned_history:
                if role == "user":
                    cleaned_history.append(m)
            else:
                last_role = "assistant" if cleaned_history[-1]["role"] == "model" else "user"
                if role != last_role:
                    cleaned_history.append(m)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in cleaned_history:
            role = "assistant" if m["role"] == "model" else "user"
            messages.append({"role": role, "content": m["text"]})

        reply = get_sealion_response(messages)
        if not reply:
            reply = "ဟင့်... မီးမီး နားမလည်လိုက်ဘူး 🥺"

        chat_histories[chat_id].append({"role": "model", "text": reply})
        await msg.reply_text(reply)

    except Exception as e:
        logger.exception("SEA-LION Error")
        if chat_histories[chat_id] and chat_histories[chat_id][-1]["role"] == "user":
            chat_histories[chat_id].pop()
        await msg.reply_text("ဟင့်... ခေါင်းနည်းနည်းမူးနေလို့ပါ ခနလေးနော် 🥺")

# ============================================================
# 9. FLASK KEEP-ALIVE SERVER (Render အတွက်)
# ============================================================
flask_app = Flask(__name__)

@flask_app.route("/")
@flask_app.route("/health")
def health_check():
    return "LiLi Bot is running! 🌸"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ============================================================
# 10. MAIN
# ============================================================
def main():
    # Flask ကို နောက်ခံ thread မှာ run
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask health server started.")

    # Telegram bot ကို main thread မှာ run
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler(["lili", "LiLi"], cmd_lili_on))
    app.add_handler(CommandHandler(["byebyelili", "ByeByeLiLi"], cmd_lili_off))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("whatiremember", cmd_whatiremember))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_message))

    print("🌸 LiLi SEA-LION Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
