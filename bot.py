import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")

# finto calcolatore di calorie (poi lo colleghiamo a Nutritionix)
def stima_calorie(testo):
    return 100  # placeholder

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Scrivimi cosa hai mangiato e lo registro nel diario 🍎")

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    food = update.message.text
    kcal = stima_calorie(food)
    # per ora solo risposta, poi aggiungiamo il salvataggio
    await update.message.reply_text(f"Ho registrato: {food} (~{kcal} kcal)")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))
    app.run_polling()