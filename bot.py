import os
import re
from datetime import datetime

from groq import Groq
from supabase import create_client
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ---------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------

TOKEN = os.getenv("BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

MODEL_NAME = "deepseek-r1-distill-llama-70b"


# ---------------------------------------------------------
# STIMA CALORIE (DeepSeek R1 — ragionamento naturale)
# ---------------------------------------------------------

def stima_calorie(cibo: str) -> int:
    prompt = f"""
Sei un nutrizionista italiano. Stima le calorie totali del seguente alimento o piatto:

\"{cibo}\"

Linee guida:
- Considera una porzione standard italiana.
- Se è un piatto completo (pizza, pasta, panino), considera la porzione intera.
- Se è un alimento singolo (mela, yogurt), usa valori realistici.
- Se ci sono più ingredienti, somma le calorie.
- Rispondi SOLO con un numero intero (le kcal stimate), senza testo aggiuntivo.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}]
        )

        testo = response.choices[0].message.content.strip()

        # DeepSeek sometimes adds reasoning tags like <think>...</think>
        testo = re.sub(r"<think>.*?</think>", "", testo, flags=re.DOTALL).strip()

        match = re.search(r"\d+", testo)
        if match:
            return int(match.group(0))

        return 300
    except Exception as e:
        print("Errore DeepSeek:", e)
        return 300


# ---------------------------------------------------------
# RICONOSCIMENTO DEL PASTO
# ---------------------------------------------------------

def riconosci_pasto(testo: str) -> str:
    testo = testo.lower()

    if "colazione" in testo or "stamattina" in testo or "mattina" in testo:
        return "colazione"
    if "pranzo" in testo or "mezzogiorno" in testo:
        return "pranzo"
    if "cena" in testo or "stasera" in testo or "sera" in testo:
        return "cena"

    return "non specificato"


# ---------------------------------------------------------
# ESTRAZIONE DEL CIBO (versione corretta)
# ---------------------------------------------------------

def estrai_cibo(testo: str) -> str:
    testo = testo.lower()

    frasi_da_togliere = [
        "ho mangiato",
        "oggi",
        "stamattina",
        "stasera",
        "a pranzo",
        "a cena",
        "per",
        "ho preso"
    ]

    for f in frasi_da_togliere:
        testo = testo.replace(f, "")

    return testo.strip()


# ---------------------------------------------------------
# SALVATAGGIO SU SUPABASE
# ---------------------------------------------------------

def salva_pasto(tipo: str, descrizione: str, kcal: int):
    now = datetime.now()

    supabase.table("pasti").insert({
        "ora": now.isoformat(),
        "pasto": tipo,
        "descrizione": descrizione,
        "kcal": kcal
    }).execute()


# ---------------------------------------------------------
# COMANDI TELEGRAM
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Ciao! Scrivimi cosa hai mangiato e lo registro nel diario con una stima delle calorie 🍎"
    )


async def test_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now()

        supabase.table("pasti").insert({
            "ora": now.isoformat(),
            "pasto": "test",
            "descrizione": "test di connessione",
            "kcal": 0
        }).execute()

        await update.message.reply_text("Test riuscito! Controlla Supabase 👍")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        risposta = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": "Dimmi un numero a caso tra 1 e 1000."}]
        )
        testo = risposta.choices[0].message.content
        testo = re.sub(r"<think>.*?</think>", "", testo, flags=re.DOTALL).strip()
        await update.message.reply_text("Risposta DeepSeek: " + testo)
    except Exception as e:
        await update.message.reply_text(f"Errore DeepSeek: {e}")


# ---------------------------------------------------------
# LOGICA PRINCIPALE
# ---------------------------------------------------------

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text

    tipo = riconosci_pasto(testo)
    cibo = estrai_cibo(testo)
    kcal = stima_calorie(cibo)

    salva_pasto(tipo, cibo, kcal)

    await update.message.reply_text(
        f"Registrato {tipo}: {cibo} (~{kcal} kcal)"
    )


# ---------------------------------------------------------
# AVVIO BOT
# ---------------------------------------------------------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_sheet))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))

    app.run_polling()