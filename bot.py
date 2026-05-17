import os
import json
import re
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

from supabase import create_client
from faster_whisper import WhisperModel
from pydub import AudioSegment

# -----------------------------
# CONFIGURAZIONE
# -----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

MODEL_NAME = "llama3-8b-8192"
from groq import Groq
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# -----------------------------
# WHISPER
# -----------------------------

whisper_model = WhisperModel("small", device="cpu")

def trascrivi_audio(percorso):
    segments, info = whisper_model.transcribe(percorso, beam_size=5)
    testo = " ".join([seg.text for seg in segments])
    return testo.strip()

# -----------------------------
# AI PARSER MIGLIORATO
# -----------------------------

def ai_parse_intent(testo: str) -> dict:
    prompt = f"""
Sei un parser rigoroso per un diario alimentare.
Devi analizzare il messaggio dell'utente e restituire SOLO un JSON valido.

### FORMATO OBBLIGATORIO
{{
  "intento": "aggiungi | chiedi_calorie | riepilogo_giorno | riepilogo_pasto | somma_giorno | somma_pasto | cancella | altro",
  "pasto": "colazione | pranzo | cena | null",
  "data": "oggi | ieri | null",
  "alimenti": [
    {{
      "alimento": "string o null",
      "quantita": "string o null"
    }}
  ]
}}

### REGOLE IMPORTANTI

1. Se il messaggio contiene:
   - "oggi", "di oggi" → data = "oggi"
   - "ieri", "di ieri" → data = "ieri"

2. Se contiene:
   - "colazione" → pasto = "colazione"
   - "pranzo" → pasto = "pranzo"
   - "cena" → pasto = "cena"

3. Se contiene:
   - "riepilogo", "recap", "resoconto", "mostrami"
     *Se NON è specificato un pasto → riepilogo_giorno*
     *Se è specificato un pasto → riepilogo_pasto*

4. Se contiene:
   - "totale", "quante calorie", "somma"
     → somma_giorno o somma_pasto

5. Se contiene:
   - "aggiungi", "metti", "registra", "ho mangiato"
     → intento = aggiungi

6. Se contiene:
   - "togli", "rimuovi", "elimina", "cancella"
     → intento = cancella

7. NON inventare alimenti.

### MESSAGGIO UTENTE
"{testo}"

### RISPOSTA
Rispondi SOLO con il JSON.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()

        try:
            return json.loads(raw)
        except:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group(0))

    except:
        pass

    return {
        "intento": "altro",
        "pasto": None,
        "data": None,
        "alimenti": []
    }

# -----------------------------
# UTILS
# -----------------------------

def riconosci_data_da_intent(intent_data, testo):
    oggi = datetime.now().strftime("%Y-%m-%d")
    ieri = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    if intent_data == "oggi":
        return oggi
    if intent_data == "ieri":
        return ieri

    if "oggi" in testo:
        return oggi
    if "ieri" in testo:
        return ieri

    return oggi

# -----------------------------
# CALORIE (AI)
# -----------------------------

def ai_stima_calorie(alimento, quantita):
    prompt = f"""
Stima le calorie dell'alimento seguente.

Alimento: {alimento}
Quantità: {quantita}

Rispondi SOLO con un numero intero, senza testo aggiuntivo.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"[^\d]", "", raw)
        return int(raw) if raw else 0
    except:
        return 0

# -----------------------------
# AGGIUNTA PASTI
# -----------------------------

async def aggiungi_ai(update: Update, testo: str, intent: dict):
    pasto = intent.get("pasto")
    data = riconosci_data_da_intent(intent.get("data"), testo)

    if not pasto:
        await update.message.reply_text("Non ho capito il pasto (colazione, pranzo, cena).")
        return

    alimenti = intent.get("alimenti", [])
    if not alimenti:
        await update.message.reply_text("Non ho capito cosa hai mangiato.")
        return

    totale_kcal = 0
    righe = []

    for a in alimenti:
        nome = a.get("alimento")
        quantita = a.get("quantita") or ""

        kcal = ai_stima_calorie(nome, quantita)
        totale_kcal += kcal

        descr = f"{quantita} {nome}".strip()
        righe.append(f"- {descr} ({kcal} kcal)")

        supabase.table("pasti").insert({
            "data": data,
            "pasto": pasto,
            "descrizione": descr,
            "kcal": kcal,
            "ora": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }).execute()

    risposta = f"{pasto.capitalize()} di {data}:\n\n" + "\n".join(righe)
    risposta += f"\nTotale: {totale_kcal} kcal"

    await update.message.reply_text(risposta)

# -----------------------------
# RIEPILOGO GIORNO
# -----------------------------

async def riepilogo_giorno(update: Update, testo: str, intent: dict):
    data = riconosci_data_da_intent(intent.get("data"), testo)

    res = supabase.table("pasti").select("*").eq("data", data).execute()
    pasti = res.data or []

    if not pasti:
        await update.message.reply_text(f"Nessun pasto registrato per {data}.")
        return

    risposta = f"Riepilogo del {data}:\n\n"
    totale = 0

    for r in pasti:
        risposta += f"- {r['pasto']}: {r['descrizione']} ({r['kcal']} kcal)\n"
        totale += r["kcal"]

    risposta += f"\nTotale giornaliero: {totale} kcal"
    await update.message.reply_text(risposta)

# -----------------------------
# RIEPILOGO PASTO
# -----------------------------

async def riepilogo_pasto(update: Update, testo: str, intent: dict):
    pasto = intent.get("pasto")
    data = riconosci_data_da_intent(intent.get("data"), testo)

    if not pasto:
        await update.message.reply_text("Non ho capito quale pasto vuoi vedere.")
        return

    res = supabase.table("pasti").select("*").eq("data", data).eq("pasto", pasto).execute()
    pasti = res.data or []

    if not pasti:
        await update.message.reply_text(f"Nessun elemento per {pasto} di {data}.")
        return

    risposta = f"{pasto.capitalize()} di {data}:\n\n"
    totale = 0

    for r in pasti:
        risposta += f"- {r['descrizione']} ({r['kcal']} kcal)\n"
        totale += r["kcal"]

    risposta += f"\nTotale: {totale} kcal"
    await update.message.reply_text(risposta)

# -----------------------------
# CANCELLAZIONE MIGLIORATA
# -----------------------------

async def cancella_ai(update: Update, testo: str, intent: dict):
    intent_alimento = None

    if intent.get("alimento"):
        intent_alimento = intent["alimento"]
    else:
        arr = intent.get("alimenti", [])
        if arr and arr[0].get("alimento"):
            intent_alimento = arr[0]["alimento"]

    if not intent_alimento:
        await update.message.reply_text("Non ho capito cosa vuoi cancellare.")
        return

    data = riconosci_data_da_intent(intent.get("data"), testo)

    res = supabase.table("pasti").select("*").eq("data", data).execute()
    candidati = res.data or []

    a = intent_alimento.lower()
    candidati = [r for r in candidati if a in r["descrizione"].lower()]

    if not candidati:
        await update.message.reply_text(f"Non ho trovato '{intent_alimento}' da cancellare.")
        return

    if len(candidati) == 1:
        r = candidati[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()
        await update.message.reply_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
        return

    keyboard = []
    for r in candidati:
        label = f"{r['descrizione']} - {r['ora'][11:16]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"del_{r['id']}")])

    await update.message.reply_text(
        f"Ho trovato più elementi che contengono '{intent_alimento}'. Quale vuoi cancellare?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# -----------------------------
# LOGICA PRINCIPALE
# -----------------------------

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    testo = context.user_data.get("voice_text") or update.message.text
    testo = testo.strip().lower()

    intent = ai_parse_intent(testo)
    azione = intent.get("intento")

    if azione == "aggiungi":
        await aggiungi_ai(update, testo, intent)
    elif azione == "riepilogo_giorno":
        await riepilogo_giorno(update, testo, intent)
    elif azione == "riepilogo_pasto":
        await riepilogo_pasto(update, testo, intent)
    elif azione == "cancella":
        await cancella_ai(update, testo, intent)
    else:
        await update.message.reply_text("Non ho capito cosa vuoi fare.")

    context.user_data["voice_text"] = None

# -----------------------------
# CALLBACK CANCELLAZIONE
# -----------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("del_"):
        id_da_cancellare = data.replace("del_", "")
        supabase.table("pasti").delete().eq("id", id_da_cancellare).execute()
        await query.edit_message_text("Elemento cancellato.")
        return

# -----------------------------
# GESTIONE VOCALI
# -----------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    file = await update.message.voice.get_file()
    percorso = f"/tmp/{file.file_id}.ogg"
    await file.download_to_drive(percorso)

    wav_path = percorso.replace(".ogg", ".wav")
    AudioSegment.from_file(percorso).export(wav_path, format="wav")

    testo = trascrivi_audio(wav_path)

    await update.message.reply_text(f"🎤 Hai detto:\n{testo}")

    testo_norm = testo.lower().strip().replace(".", "")
    context.user_data["voice_text"] = testo_norm

    await log_food(update, context)

# -----------------------------
# AVVIO BOT
# -----------------------------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot avviato!")
    app.run_polling()

if __name__ == "__main__":
    main()