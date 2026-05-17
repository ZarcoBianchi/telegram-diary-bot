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
# AI: CLASSIFICAZIONE INTENTO (SOLO AI, USATA DOPO FALLBACK)
# ---------------------------------------------------------

def classify_intent_ai(testo: str) -> dict:
    prompt = f"""
Sei un assistente che interpreta messaggi per un diario alimentare.

Devi restituire un JSON con questo schema:

{{
  "intento": "...",          // aggiungi, cancella, riepilogo_pasto, riepilogo_giorno, somma_pasto, somma_giorno, non_chiaro
  "alimento": "...",         // opzionale
  "pasto": "...",            // colazione, pranzo, cena, non_specificato
  "data": "...",             // oggi, ieri, altro, oppure vuoto
  "testo_data": "..."        // testo originale da cui dedurre la data, se serve
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

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {"intento": "non_chiaro"}

        raw_json = match.group(0)

        try:
            data = json.loads(raw_json)
        except:
            return {"intento": "non_chiaro"}

        if not isinstance(data, dict):
            return {"intento": "non_chiaro"}

        if "intento" not in data or not data["intento"]:
            return {"intento": "non_chiaro"}

        return data

    except:
        return {"intento": "non_chiaro"}


# ---------------------------------------------------------
# FALLBACK TESTUALE AGGRESSIVO (PRIMA DELL'AI)
# ---------------------------------------------------------

def fallback_intent(testo: str) -> dict | None:
    t = testo.lower()

    # cancella
    if any(k in t for k in ["cancella", "elimina", "rimuovi"]):
        return {"intento": "cancella"}

    # somma pasto
    if "quante calorie" in t and any(p in t for p in ["pranzo", "cena", "colazione"]):
        pasto = "pranzo"
        if "colazione" in t:
            pasto = "colazione"
        elif "cena" in t:
            pasto = "cena"
        data = "oggi"
        if "ieri" in t:
            data = "ieri"
        return {"intento": "somma_pasto", "pasto": pasto, "data": data, "testo_data": testo}

    # somma giorno
    if "quante calorie" in t and any(k in t for k in ["oggi", "ieri"]):
        data = "oggi"
        if "ieri" in t:
            data = "ieri"
        return {"intento": "somma_giorno", "data": data, "testo_data": testo}

    # riepilogo pasto
    if "cosa ho mangiato" in t and any(p in t for p in ["pranzo", "cena", "colazione"]):
        pasto = "pranzo"
        if "colazione" in t:
            pasto = "colazione"
        elif "cena" in t:
            pasto = "cena"
        data = "oggi"
        if "ieri" in t:
            data = "ieri"
        return {"intento": "riepilogo_pasto", "pasto": pasto, "data": data, "testo_data": testo}

    # riepilogo giorno
    if "cosa ho mangiato" in t and any(k in t for k in ["oggi", "ieri"]):
        data = "oggi"
        if "ieri" in t:
            data = "ieri"
        return {"intento": "riepilogo_giorno", "data": data, "testo_data": testo}

    # aggiungi
    if any(k in t for k in ["ho mangiato", "per pranzo ho", "per cena ho", "per colazione ho", "ho bevuto", "aggiungi"]):
        return {"intento": "aggiungi"}

    return None


# ---------------------------------------------------------
# ESTRAZIONE QUANTITÀ
# ---------------------------------------------------------

def estrai_quantita(testo: str):
    t = testo.lower()

    # grammi
    m = re.search(r"(\d+)\s*(g|grammi|grammo)", t)
    if m:
        return {"tipo": "grammi", "valore": float(m.group(1))}

    # ml / cl / litri
    m = re.search(r"(\d+)\s*(ml|millilitri|cl|l|litri)", t)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit == "cl":
            val *= 10
        if unit in ["l", "litri"]:
            val *= 1000
        return {"tipo": "ml", "valore": val}

    # pezzi generici
    m = re.search(r"(\d+)\s*(pezzi|biscotti|uova|fette)", t)
    if m:
        return {"tipo": "pezzi", "valore": float(m.group(1)), "unita": m.group(2)}

    # cucchiaio
    if "cucchiaio" in t:
        return {"tipo": "grammi", "valore": 15}

    # cucchiaino
    if "cucchiaino" in t:
        return {"tipo": "grammi", "valore": 5}

    # bicchiere
    if "mezzo bicchiere" in t:
        return {"tipo": "ml", "valore": 75}
    if "bicchiere" in t:
        return {"tipo": "ml", "valore": 150}

    # porzione
    if "mezza porzione" in t:
        return {"tipo": "porzione", "valore": 0.5}
    if "porzione" in t:
        return {"tipo": "porzione", "valore": 1}

    return None


# ---------------------------------------------------------
# ESTRAZIONE ALIMENTO (REGEX → AI)
# ---------------------------------------------------------

def estrai_alimento_regex(testo: str) -> str | None:
    t = testo.lower()

    patterns = [
        r"ho mangiato (.+)",
        r"per pranzo ho (.+)",
        r"per cena ho (.+)",
        r"per colazione ho (.+)",
        r"ho bevuto (.+)",
        r"aggiungi (.+)",
    ]

    for p in patterns:
        m = re.search(p, t)
        if m:
            return m.group(1).strip()

    return None


def estrai_alimento_ai(testo: str) -> str | None:
    prompt = f"""
Dal seguente messaggio estrai SOLO il nome dell'alimento o bevanda consumata.

Messaggio: "{testo}"

Rispondi SOLO con il nome dell'alimento, senza altre parole.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    except:
        return None


def estrai_alimento(testo: str, intent: dict) -> str:
    if intent.get("alimento"):
        return intent["alimento"].strip()

    a = estrai_alimento_regex(testo)
    if a:
        return a

    a = estrai_alimento_ai(testo)
    if a:
        return a

    return testo.strip()


# ---------------------------------------------------------
# SUPABASE: FOODS (TABELLA DINAMICA)
# ---------------------------------------------------------

def cerca_alimento_foods(nome: str):
    nome = nome.lower().strip()
    res = supabase.table("foods").select("*").eq("name", nome).execute()
    if res.data:
        return res.data[0]
    return None


def salva_alimento_foods(nome: str, kcal_100g=None, kcal_100ml=None, kcal_unit=None, grams_unit=None, ml_unit=None, source="AI"):
    nome = nome.lower().strip()
    supabase.table("foods").insert({
        "name": nome,
        "kcal_per_100g": int(kcal_100g) if kcal_100g is not None else None,
        "kcal_per_100ml": int(kcal_100ml) if kcal_100ml is not None else None,
        "kcal_per_unit": int(kcal_unit) if kcal_unit is not None else None,
        "grams_per_unit": grams_unit,
        "ml_per_unit": ml_unit,
        "source": source,
    }).execute()


def aggiorna_alimento_foods(nome: str, kcal_100g=None, kcal_100ml=None, kcal_unit=None, grams_unit=None, ml_unit=None, source="user"):
    nome = nome.lower().strip()
    supabase.table("foods").update({
        "kcal_per_100g": int(kcal_100g) if kcal_100g is not None else None,
        "kcal_per_100ml": int(kcal_100ml) if kcal_100ml is not None else None,
        "kcal_per_unit": int(kcal_unit) if kcal_unit is not None else None,
        "grams_per_unit": grams_unit,
        "ml_per_unit": ml_unit,
        "source": source,
    }).eq("name", nome).execute()


# ---------------------------------------------------------
# AI: STIMA VALORI NUTRIZIONALI PER ALIMENTO NUOVO
# ---------------------------------------------------------

def ai_stima_kcal(alimento: str):
    prompt = f"""
Stima i valori nutrizionali del seguente alimento:

"{alimento}"

Rispondi SOLO con un JSON nel formato:

{{
  "kcal_per_100g": ...,
  "kcal_per_100ml": ...,
  "kcal_per_unit": ...,
  "grams_per_unit": ...,
  "ml_per_unit": ...
}}

Metti null per i campi non applicabili.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        return {
            "kcal_per_100g": data.get("kcal_per_100g"),
            "kcal_per_100ml": data.get("kcal_per_100ml"),
            "kcal_per_unit": data.get("kcal_per_unit"),
            "grams_per_unit": data.get("grams_per_unit"),
            "ml_per_unit": data.get("ml_per_unit"),
        }
    except:
        return None


# ---------------------------------------------------------
# CALCOLO CALORIE (AI + FALLBACK MATEMATICO)
# ---------------------------------------------------------

def calcola_kcal(alimento_info, quantita):
    tipo = quantita["tipo"]
    val = quantita["valore"]

    # 1) grammi
    if tipo == "grammi" and alimento_info.get("kcal_per_100g"):
        return int((alimento_info["kcal_per_100g"] * val) / 100)

    # 2) ml
    if tipo == "ml" and alimento_info.get("kcal_per_100ml"):
        return int((alimento_info["kcal_per_100ml"] * val) / 100)

    # 3) pezzi / porzione
    if tipo in ["pezzi", "porzione"] and alimento_info.get("kcal_per_unit"):
        return int(alimento_info["kcal_per_unit"] * val)

    # fallback generico
    return 200


# ---------------------------------------------------------
# STIMA CALORIE SENZA QUANTITÀ (BACKUP)
# ---------------------------------------------------------

def stima_calorie_senza_quantita(cibo: str) -> int:
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
# SUPABASE: PASTI
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


async def cancella_ai(update: Update, testo: str, intent: dict):
    t = testo.lower()

    if "ieri" in t:
        d = date.today() - timedelta(days=1)
    elif "oggi" in t:
        d = date.today()
    else:
        d = parse_natural_date(testo)

    data_str = date_to_iso(d)
    res = supabase.table("pasti").select("*").execute()
    candidati = [r for r in res.data if r["ora"].startswith(data_str)]

    if not candidati:
        await update.message.reply_text("Non ho trovato nulla da cancellare.")
        return

    alimento = intent.get("alimento", "").strip()
    if not alimento:
        alimento = estrai_alimento(testo, intent)

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
    ts = ud.get("pending_timestamp")
    if not ts:
        return False
    return (time.time() - ts) > PENDING_TIMEOUT


async def reset_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ud["pending_add"] = None
    ud["pending_add_ask_pasto"] = None
    ud["pending_new_food"] = None
    ud["awaiting_manual_kcal_for_food"] = None
    ud["pending_timestamp"] = None
    if update.message:
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

    if check_pending_timeout(context):
        await query.edit_message_text("Richiesta scaduta.")
        await reset_pending(update, context)
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

    # conferma pasto suggerito
    if data == "add_confirm_yes":
        pending = ud.get("pending_add")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa.")
            return
        alimento = pending["alimento"]
        pasto = pending["pasto_suggerito"]
        d = pending["data"]
        quantita = pending.get("quantita")
        descrizione = pending["descrizione"]

        # calcolo kcal usando foods se possibile
        info = cerca_alimento_foods(alimento)
        if quantita and info:
            kcal = calcola_kcal(info, quantita)
        elif info and not quantita:
            # se non c'è quantita ma abbiamo kcal_per_unit
            if info.get("kcal_per_unit"):
                kcal = int(info["kcal_per_unit"])
            else:
                kcal = stima_calorie_senza_quantita(descrizione)
        else:
            kcal = stima_calorie_senza_quantita(descrizione)

        salva_pasto(pasto, descrizione, kcal, d)
        ud["pending_add"] = None
        ud["pending_timestamp"] = None
        await query.edit_message_text(f"Registrato {pasto}: {descrizione} ({kcal} kcal)")
        return

    if data == "add_confirm_no":
        pending = ud.get("pending_add")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa.")
            return
        alimento = pending["alimento"]
        d = pending["data"]
        quantita = pending.get("quantita")
        descrizione = pending["descrizione"]

        ud["pending_add"] = None
        ud["pending_add_ask_pasto"] = {
            "alimento": alimento,
            "data": d,
            "quantita": quantita,
            "descrizione": descrizione,
        }
        ud["pending_timestamp"] = time.time()

        keyboard = [
            [
                InlineKeyboardButton("Colazione", callback_data="add_set_colazione"),
                InlineKeyboardButton("Pranzo", callback_data="add_set_pranzo"),
                InlineKeyboardButton("Cena", callback_data="add_set_cena"),
            ]
        ]
        await query.edit_message_text(
            f"Ok, per quale pasto hai mangiato {descrizione}?",
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
        quantita = pending.get("quantita")
        descrizione = pending["descrizione"]

        if data == "add_set_colazione":
            pasto = "colazione"
        elif data == "add_set_pranzo":
            pasto = "pranzo"
        else:
            pasto = "cena"

        info = cerca_alimento_foods(alimento)
        if quantita and info:
            kcal = calcola_kcal(info, quantita)
        elif info and not quantita:
            if info.get("kcal_per_unit"):
                kcal = int(info["kcal_per_unit"])
            else:
                kcal = stima_calorie_senza_quantita(descrizione)
        else:
            kcal = stima_calorie_senza_quantita(descrizione)

        salva_pasto(pasto, descrizione, kcal, d)

        ud["pending_add_ask_pasto"] = None
        ud["pending_timestamp"] = None

        await query.edit_message_text(f"Registrato {pasto}: {descrizione} ({kcal} kcal)")
        return

    # conferma nuovo alimento (foods)
    if data == "food_confirm_yes":
        pending_food = ud.get("pending_new_food")
        if not pending_food:
            await query.edit_message_text("Nessun alimento in attesa.")
            return

        alimento = pending_food["alimento"]
        info_ai = pending_food["info_ai"]
        quantita = pending_food["quantita"]
        pasto = pending_food["pasto"]
        d = pending_food["data"]
        descrizione = pending_food["descrizione"]

        salva_alimento_foods(
            alimento,
            kcal_100g=info_ai.get("kcal_per_100g"),
            kcal_100ml=info_ai.get("kcal_per_100ml"),
            kcal_unit=info_ai.get("kcal_per_unit"),
            grams_unit=info_ai.get("grams_per_unit"),
            ml_unit=info_ai.get("ml_per_unit"),
            source="AI",
        )

        info = cerca_alimento_foods(alimento)
        if quantita and info:
            kcal = calcola_kcal(info, quantita)
        else:
            kcal = stima_calorie_senza_quantita(descrizione)

        salva_pasto(pasto, descrizione, kcal, d)

        ud["pending_new_food"] = None
        ud["pending_timestamp"] = None

        await query.edit_message_text(
            f"Ho salvato “{alimento}” in foods e registrato {pasto}: {descrizione} ({kcal} kcal)"
        )
        return

    if data == "food_confirm_no":
        pending_food = ud.get("pending_new_food")
        if not pending_food:
            await query.edit_message_text("Nessun alimento in attesa.")
            return

        alimento = pending_food["alimento"]
        ud["awaiting_manual_kcal_for_food"] = pending_food
        ud["pending_new_food"] = None
        ud["pending_timestamp"] = time.time()

        await query.edit_message_text(
            f"Ok, dimmi tu quante kcal per 100g ha “{alimento}”.\n"
            f"Rispondi solo con un numero intero (es: 550)."
        )
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
# INTENT FINALE (FALLBACK → AI)
# ---------------------------------------------------------

def get_intent(testo: str) -> dict:
    fb = fallback_intent(testo)
    if fb:
        return fb
    return classify_intent_ai(testo)


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

    # timeout pending
    if check_pending_timeout(context):
        await reset_pending(update, context)

    # gestione risposta manuale kcal per nuovo alimento
    if ud.get("awaiting_manual_kcal_for_food"):
        pending_food = ud["awaiting_manual_kcal_for_food"]
        try:
            kcal_100g = int(re.search(r"\d+", testo).group(0))
        except:
            await update.message.reply_text("Non ho capito il numero. Scrivi solo le kcal per 100g, es: 550.")
            return

        alimento = pending_food["alimento"]
        quantita = pending_food["quantita"]
        pasto = pending_food["pasto"]
        d = pending_food["data"]
        descrizione = pending_food["descrizione"]

        salva_alimento_foods(
            alimento,
            kcal_100g=kcal_100g,
            kcal_100ml=None,
            kcal_unit=None,
            grams_unit=None,
            ml_unit=None,
            source="user",
        )

        info = cerca_alimento_foods(alimento)
        if quantita and info:
            kcal = calcola_kcal(info, quantita)
        else:
            kcal = stima_calorie_senza_quantita(descrizione)

        salva_pasto(pasto, descrizione, kcal, d)

        ud["awaiting_manual_kcal_for_food"] = None
        ud["pending_timestamp"] = None

        await update.message.reply_text(
            f"Perfetto, ho salvato “{alimento}” con {kcal_100g} kcal/100g e registrato {pasto}: {descrizione} ({kcal} kcal)"
        )
        return

    # se c'era un pending_add o simili ma l'utente scrive altro → reset
    if ud.get("pending_add") or ud.get("pending_add_ask_pasto") or ud.get("pending_new_food"):
        await reset_pending(update, context)

    intent = get_intent(testo)
    now = datetime.now()

    # ----------------- AGGIUNGI -----------------
    if intent["intento"] == "aggiungi":
        alimento = estrai_alimento(testo, intent)
        quantita = estrai_quantita(testo)

        pasto_txt = riconosci_pasto_da_testo(testo)
        t = testo.lower()
        if "ieri" in t:
            d = date.today() - timedelta(days=1)
        elif "oggi" in t:
            d = date.today()
        else:
            d = date.today()

        # descrizione da salvare (con quantità se presente)
        if quantita:
            if quantita["tipo"] == "grammi":
                descrizione = f"{int(quantita['valore'])}g di {alimento}"
            elif quantita["tipo"] == "ml":
                descrizione = f"{int(quantita['valore'])}ml di {alimento}"
            elif quantita["tipo"] == "pezzi":
                descrizione = f"{int(quantita['valore'])} {quantita.get('unita','pezzi')} di {alimento}"
            elif quantita["tipo"] == "porzione":
                descrizione = f"{quantita['valore']} porzione di {alimento}"
            else:
                descrizione = alimento
        else:
            descrizione = alimento

        # se il pasto è chiaro
        if pasto_txt != "non_specificato":
            info = cerca_alimento_foods(alimento)
            if quantita and info:
                kcal = calcola_kcal(info, quantita)
            elif info and not quantita:
                if info.get("kcal_per_unit"):
                    kcal = int(info["kcal_per_unit"])
                else:
                    kcal = stima_calorie_senza_quantita(descrizione)
            else:
                # alimento non presente in foods → AI + conferma
                info_ai = ai_stima_kcal(alimento)
                if info_ai:
                    ud["pending_new_food"] = {
                        "alimento": alimento,
                        "info_ai": info_ai,
                        "quantita": quantita,
                        "pasto": pasto_txt,
                        "data": d,
                        "descrizione": descrizione,
                    }
                    ud["pending_timestamp"] = time.time()

                    parts = []
                    if info_ai.get("kcal_per_100g") is not None:
                        parts.append(f"{info_ai['kcal_per_100g']} kcal/100g")
                    if info_ai.get("kcal_per_100ml") is not None:
                        parts.append(f"{info_ai['kcal_per_100ml']} kcal/100ml")
                    if info_ai.get("kcal_per_unit") is not None:
                        parts.append(f"{info_ai['kcal_per_unit']} kcal per unità")

                    testo_ai = ", ".join(parts) if parts else "valori stimati"

                    keyboard = [
                        [
                            InlineKeyboardButton("Sì", callback_data="food_confirm_yes"),
                            InlineKeyboardButton("No", callback_data="food_confirm_no"),
                        ]
                    ]
                    await update.message.reply_text(
                        f"Non conosco “{alimento}”.\n"
                        f"Posso salvarlo con questi valori?\n"
                        f"{testo_ai}",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    return
                else:
                    kcal = stima_calorie_senza_quantita(descrizione)

            salva_pasto(pasto_txt, descrizione, kcal, d)
            await update.message.reply_text(
                f"Registrato {pasto_txt}: {descrizione} ({kcal} kcal)"
            )
            return

        # pasto non chiaro → chiedi conferma pasto
        pasto_suggerito = suggerisci_pasto_da_orario(now)
        if pasto_suggerito == "non_specificato":
            ud["pending_add_ask_pasto"] = {
                "alimento": alimento,
                "data": d,
                "quantita": quantita,
                "descrizione": descrizione,
            }
            ud["pending_timestamp"] = time.time()
            keyboard = [
                [
                    InlineKeyboardButton("Colazione", callback_data="add_set_colazione"),
                    InlineKeyboardButton("Pranzo", callback_data="add_set_pranzo"),
                    InlineKeyboardButton("Cena", callback_data="add_set_cena"),
                ]
            ]
            await update.message.reply_text(
                f"Per quale pasto hai mangiato {descrizione}?",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        ud["pending_add"] = {
            "alimento": alimento,
            "pasto_suggerito": pasto_suggerito,
            "data": d,
            "quantita": quantita,
            "descrizione": descrizione,
        }
        ud["pending_timestamp"] = time.time()
        keyboard = [
            [
                InlineKeyboardButton("Sì", callback_data="add_confirm_yes"),
                InlineKeyboardButton("No", callback_data="add_confirm_no"),
            ]
        ]
        await update.message.reply_text(
            f"Hai mangiato {descrizione} per {pasto_suggerito}?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # ----------------- CANCELLA -----------------
    if intent["intento"] == "cancella":
        await cancella_ai(update, testo, intent)
        return

    # ----------------- RIEPILOGO PASTO -----------------
    if intent["intento"] == "riepilogo_pasto":
        pasto = intent.get("pasto") or riconosci_pasto_da_testo(testo) or "pranzo"
        t = testo.lower()
        if "ieri" in t:
            d = date.today() - timedelta(days=1)
        elif "oggi" in t:
            d = date.today()
        else:
            d = parse_natural_date(testo)
        msg = format_riepilogo_pasto(d, pasto)
        await update.message.reply_text(msg)
        return

    # ----------------- RIEPILOGO GIORNO -----------------
    if intent["intento"] == "riepilogo_giorno":
        t = testo.lower()
        if "ieri" in t:
            d = date.today() - timedelta(days=1)
        elif "oggi" in t:
            d = date.today()
        else:
            d = parse_natural_date(testo)
        msg = format_riepilogo_giorno(d)
        await update.message.reply_text(msg)
        return

    # ----------------- SOMMA GIORNO -----------------
    if intent["intento"] == "somma_giorno":
        t = testo.lower()
        if "ieri" in t:
            d = date.today() - timedelta(days=1)
        elif "oggi" in t:
            d = date.today()
        else:
            d = parse_natural_date(testo)
        tot = somma_calorie_giorno(d)
        await update.message.reply_text(f"Totale giornaliero: {tot} kcal")
        return

    # ----------------- SOMMA PASTO -----------------
    if intent["intento"] == "somma_pasto":
        pasto = intent.get("pasto") or riconosci_pasto_da_testo(testo) or "pranzo"
        t = testo.lower()
        if "ieri" in t:
            d = date.today() - timedelta(days=1)
        elif "oggi" in t:
            d = date.today()
        else:
            d = parse_natural_date(testo)
        tot = somma_calorie_pasto(d, pasto)
        await update.message.reply_text(f"Totale {pasto}: {tot} kcal")
        return

    # ----------------- NON CHIARO -----------------
    await update.message.reply_text(
        "Non ho capito bene cosa vuoi fare. Puoi dirmi cosa hai mangiato o chiedermi un riepilogo."
    )


# ---------------------------------------------------------
# AVVIO BOT
# ---------------------------------------------------------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))
    app.run_polling()