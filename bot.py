import os
import re
import json
import time
from datetime import datetime, date, timedelta
from dateutil import parser as dateparser

from groq import Groq
from supabase import create_client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------

TOKEN = os.getenv("BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

MODEL_NAME = "llama-3.3-70b-versatile"

# SOLO TU PUOI USARE IL BOT
ALLOWED_USER_ID = 1042036959

# Timeout pending (in secondi)
PENDING_TIMEOUT = 10


# ---------------------------------------------------------
# UTILS: DATE NATURALI
# ---------------------------------------------------------

def parse_natural_date(testo: str) -> date:
    testo = testo.lower()
    oggi = date.today()

    if "oggi" in testo:
        return oggi
    if "ieri" in testo:
        return oggi - timedelta(days=1)
    if "l'altro ieri" in testo or "l’altro ieri" in testo:
        return oggi - timedelta(days=2)
    if "due giorni fa" in testo:
        return oggi - timedelta(days=2)
    if "tre giorni fa" in testo:
        return oggi - timedelta(days=3)

    giorni = {
        "lunedì": 0, "lunedi": 0,
        "martedì": 1, "martedi": 1,
        "mercoledì": 2,
        "giovedì": 3, "giovedi": 3,
        "venerdì": 4, "venerdi": 4,
        "sabato": 5,
        "domenica": 6,
    }

    for g, idx in giorni.items():
        if g in testo:
            oggi_idx = oggi.weekday()
            delta = oggi_idx - idx
            if delta < 0:
                delta += 7
            return oggi - timedelta(days=delta)

    try:
        d = dateparser.parse(testo, dayfirst=True)
        if d:
            return d.date()
    except:
        pass

    return oggi


def date_to_iso(d: date) -> str:
    return d.isoformat()


# ---------------------------------------------------------
# AI: CLASSIFICAZIONE INTENTO (BLINDATA)
# ---------------------------------------------------------

def classify_intent(testo: str) -> dict:
    prompt = f"""
Sei un assistente che interpreta messaggi per un diario alimentare.

Devi restituire un JSON con questo schema:

{{
  "intento": "...",
  "alimento": "...",
  "pasto": "...",
  "data": "...",
  "testo_data": "..."
}}

Regole:
- Se un campo non è presente → NON è un errore.
- Se un campo è null → trattalo come mancante.
- Se ci sono campi extra → ignorali.
- Rispondi SOLO con un JSON valido.

Messaggio: "{testo}"
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()

        # Estrai il JSON in modo blindato
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {"intento": "non_chiaro"}

        raw_json = match.group(0)

        try:
            data = json.loads(raw_json)
        except:
            return {"intento": "non_chiaro"}

        # Normalizza
        if not isinstance(data, dict):
            return {"intento": "non_chiaro"}

        if "intento" not in data or not data["intento"]:
            return {"intento": "non_chiaro"}

        return data

    except:
        return {"intento": "non_chiaro"}


# ---------------------------------------------------------
# AI: MATCH PER CANCELLAZIONE
# ---------------------------------------------------------

def ai_match_cancel(comando: str, descrizione: str) -> bool:
    prompt = f"""
L’utente vuole cancellare qualcosa dal diario alimentare.

Comando: "{comando}"
Riga: "{descrizione}"

Rispondi SOLO con "SI" o "NO".
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        testo = response.choices[0].message.content.strip().upper()
        return "SI" in testo
    except:
        return False


# ---------------------------------------------------------
# STIMA CALORIE
# ---------------------------------------------------------

def stima_calorie(cibo: str) -> int:
    prompt = f"""
Stima le calorie totali del seguente alimento:

\"{cibo}\"

Rispondi solo con un numero intero.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        testo = response.choices[0].message.content.strip()
        match = re.search(r"\d+", testo)
        if match:
            return int(match.group(0))
        return 300
    except:
        return 300


# ---------------------------------------------------------
# RICONOSCIMENTO PASTO
# ---------------------------------------------------------

def riconosci_pasto_da_testo(testo: str) -> str:
    t = testo.lower()
    if "colazione" in t:
        return "colazione"
    if "pranzo" in t:
        return "pranzo"
    if "cena" in t:
        return "cena"
    return "non_specificato"


def suggerisci_pasto_da_orario(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 11:
        return "colazione"
    if 11 <= h < 16:
        return "pranzo"
    if 16 <= h < 23:
        return "cena"
    return "non_specificato"


# ---------------------------------------------------------
# SUPABASE
# ---------------------------------------------------------

def salva_pasto(pasto: str, descrizione: str, kcal: int, d: date):
    dt = datetime.combine(d, datetime.now().time())
    supabase.table("pasti").insert({
        "ora": dt.isoformat(),
        "pasto": pasto,
        "descrizione": descrizione,
        "kcal": kcal,
    }).execute()


def get_pasti_by_date(d: date):
    iso = date_to_iso(d)
    res = supabase.table("pasti").select("*").execute()
    return [r for r in res.data if r["ora"].startswith(iso)]


def somma_calorie_giorno(d: date) -> int:
    return sum(r["kcal"] for r in get_pasti_by_date(d))


def somma_calorie_pasto(d: date, pasto: str) -> int:
    return sum(r["kcal"] for r in get_pasti_by_date(d) if r["pasto"] == pasto)


def format_riepilogo_pasto(d: date, pasto: str) -> str:
    pasti = [r for r in get_pasti_by_date(d) if r["pasto"] == pasto]
    if not pasti:
        return f"Nessun {pasto} trovato per {d.isoformat()}."

    lines = [f"{pasto.capitalize()} di {d.isoformat()}:"]
    tot = 0
    for r in pasti:
        lines.append(f"- {r['descrizione']} ({r['kcal']} kcal)")
        tot += r["kcal"]
    lines.append("")
    lines.append(f"Totale: {tot} kcal")
    return "\n".join(lines)


def format_riepilogo_giorno(d: date) -> str:
    pasti = get_pasti_by_date(d)
    if not pasti:
        return f"Nessun pasto registrato per {d.isoformat()}."

    by_pasto = {"colazione": [], "pranzo": [], "cena": [], "non_specificato": []}
    for r in pasti:
        by_pasto.get(r["pasto"], by_pasto["non_specificato"]).append(r)

    lines = [f"Giorno: {d.isoformat()}"]
    tot = 0
    for p in ["colazione", "pranzo", "cena", "non_specificato"]:
        if by_pasto[p]:
            lines.append("")
            lines.append(f"{p.capitalize()}:")
            for r in by_pasto[p]:
                lines.append(f"- {r['descrizione']} ({r['kcal']} kcal)")
                tot += r["kcal"]
    lines.append("")
    lines.append(f"Totale: {tot} kcal")
    return "\n".join(lines)


# ---------------------------------------------------------
# CANCELLAZIONE AI
# ---------------------------------------------------------

async def cancella_ai(update: Update, testo: str, intent: dict):
    if intent.get("data") == "ieri":
        d = date.today() - timedelta(days=1)
    elif intent.get("data") == "oggi" or not intent.get("data"):
        d = date.today()
    else:
        d = parse_natural_date(intent.get("testo_data", testo))

    data_str = date_to_iso(d)
    res = supabase.table("pasti").select("*").execute()
    candidati = [r for r in res.data if r["ora"].startswith(data_str)]

    if not candidati:
        await update.message.reply_text("Non ho trovato nulla da cancellare.")
        return

    alimento = intent.get("alimento", "").strip()

    if not alimento:
        # cancella ultimo
        r = sorted(candidati, key=lambda x: x["id"], reverse=True)[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()
        await update.message.reply_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
        return

    trovati = []
    for r in candidati:
        if ai_match_cancel(testo, r["descrizione"]):
            trovati.append(r)

    if not trovati:
        await update.message.reply_text("Non ho trovato nulla da cancellare.")
        return

    if len(trovati) == 1:
        r = trovati[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()
        await update.message.reply_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
        return

    keyboard = []
    for r in trovati:
        label = f"{r['descrizione']} - {r['ora'][11:16]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"del_{r['id']}")])
    await update.message.reply_text(
        "Ho trovato più elementi simili. Quale vuoi cancellare?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------
# RESET PENDING
# ---------------------------------------------------------

def check_pending_timeout(context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    if "pending_timestamp" not in ud or not ud["pending_timestamp"]:
        return False

    if time.time() - ud["pending_timestamp"] > PENDING_TIMEOUT:
        return True

    return False


async def reset_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data

    alimento = None
    if ud.get("pending_add"):
        alimento = ud["pending_add"].get("alimento")
    if ud.get("pending_add_ask_pasto"):
        alimento = ud["pending_add_ask_pasto"].get("alimento")

    ud["pending_add"] = None
    ud["pending_add_ask_pasto"] = None
    ud["pending_timestamp"] = None

    if alimento:
        await update.message.reply_text(
            f"Ho annullato la richiesta precedente perché non hai risposto.\n"
            f"Stavi aggiungendo: {alimento}"
        )
    else:
        await update.message.reply_text(
            "Ho annullato la richiesta precedente perché non hai risposto."
        )


# ---------------------------------------------------------
# CALLBACK BOTTONI
# ---------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    ud = context.user_data

    await query.answer()

    # timeout?
    if check_pending_timeout(context):
        await query.edit_message_text("Richiesta scaduta.")
        ud["pending_add"] = None
        ud["pending_add_ask_pasto"] = None
        ud["pending_timestamp"] = None
        return

    # cancellazione
    if data.startswith("del_"):
        id_da_cancellare = int(data.replace("del_", ""))
        res = supabase.table("pasti").select("*").eq("id", id_da_cancellare).execute()
        if not res.data:
            await query.edit_message_text("Elemento già cancellato.")
            return
        r = res.data[0]
        supabase.table("pasti").delete().eq("id", id_da_cancellare).execute()
        await query.edit_message_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
        return

    # conferma aggiunta
    if data == "add_confirm_yes":
        pending = ud.get("pending_add")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa.")
            return
        alimento = pending["alimento"]
        pasto = pending["pasto_suggerito"]
        d = pending["data"]
        kcal = stima_calorie(alimento)
        salva_pasto(pasto, alimento, kcal, d)
        ud["pending_add"] = None
        ud["pending_timestamp"] = None
        await query.edit_message_text(f"Registrato {pasto}: {alimento} ({kcal} kcal)")
        return

    if data == "add_confirm_no":
        pending = ud.get("pending_add")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa.")
            return
        alimento = pending["alimento"]
        d = pending["data"]

        ud["pending_add"] = None
        ud["pending_add_ask_pasto"] = {"alimento": alimento, "data": d}
        ud["pending_timestamp"] = time.time()

        keyboard = [
            [
                InlineKeyboardButton("Colazione", callback_data="add_set_colazione"),
                InlineKeyboardButton("Pranzo", callback_data="add_set_pranzo"),
                InlineKeyboardButton("Cena", callback_data="add_set_cena"),
            ]
        ]
        await query.edit_message_text(
            f"Ok, per quale pasto hai mangiato {alimento}?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data in ["add_set_colazione", "add_set_pranzo", "add_set_cena"]:
        pending = ud.get("pending_add_ask_pasto")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa.")
            return

        alimento = pending["alimento"]
        d = pending["data"]

        if data == "add_set_colazione":
            pasto = "colazione"
        elif data == "add_set_pranzo":
            pasto = "pranzo"
        else:
            pasto = "cena"

        kcal = stima_calorie(alimento)
        salva_pasto(pasto, alimento, kcal, d)

        ud["pending_add_ask_pasto"] = None
        ud["pending_timestamp"] = None

        await query.edit_message_text(f"Registrato {pasto}: {alimento} ({kcal} kcal)")
        return


# ---------------------------------------------------------
# COMANDI
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    await update.message.reply_text(
        "Diario alimentare attivo. Scrivimi cosa hai mangiato o chiedimi un riepilogo."
    )


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    risposta = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": "Dimmi un numero a caso tra 1 e 1000."}],
    )
    testo = risposta.choices[0].message.content.strip()
    await update.message.reply_text("Risposta LLaMA: " + testo)


# ---------------------------------------------------------
# LOGICA PRINCIPALE
# ---------------------------------------------------------

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    testo = update.message.text.strip()
    ud = context.user_data

    # Se pending scaduto → reset
    if check_pending_timeout(context):
        await reset_pending(update, context)

    # Se pending attivo e l'utente scrive testo → reset
    if ud.get("pending_add") or ud.get("pending_add_ask_pasto"):
        await reset_pending(update, context)

    intent = classify_intent(testo)
    now = datetime.now()

    # aggiungi
    if intent["intento"] == "aggiungi":
        alimento = intent.get("alimento", testo).strip()
        pasto_txt = riconosci_pasto_da_testo(testo)

        # data
        if intent.get("data") == "ieri":
            d = date.today() - timedelta(days=1)
        elif intent.get("data") == "