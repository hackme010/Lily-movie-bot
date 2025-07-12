import os
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    JobQueue
)
from fuzzywuzzy import process
import sqlite3
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("8136955298:AAHQq0bFHUhy0ZytLw6zgJty4pZEHkgUaGQ")
PRIVATE_CHANNEL_ID = int(os.getenv("1002654782182"))
PUBLIC_CHANNEL_ID = int(os.getenv("1002796610784"))
AUTO_DELETE_SECONDS = 3 * 60 * 60  # 3 hours

# Database setup
def init_db():
    conn = sqlite3.connect('movies.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        private_msg_id INTEGER UNIQUE
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ratings (
        user_id INTEGER NOT NULL,
        movie_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, movie_id),
        FOREIGN KEY (movie_id) REFERENCES movies(id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Fuzzy search movies
async def search_movie(query):
    conn = sqlite3.connect('movies.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM movies")
    movies = cursor.fetchall()
    conn.close()
    
    if not movies:
        return None
    
    titles = [m[1] for m in movies]
    best_match = process.extractOne(query, titles)
    
    if best_match[1] > 65:  # Similarity threshold
        return next(m for m in movies if m[1] == best_match[0])
    return None

# Get/save ratings
async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, movie_id, rating = query.data.split('_')
    user_id = query.from_user.id
    
    conn = sqlite3.connect('movies.db')
    cursor = conn.cursor()
    
    # Upsert rating
    cursor.execute('''
    INSERT OR REPLACE INTO ratings (user_id, movie_id, rating, timestamp)
    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (user_id, int(movie_id), int(rating)))
    
    conn.commit()
    conn.close()
    
    # Update rating display
    await update_rating_message(context, int(movie_id), query.message)

async def update_rating_message(context, movie_id, message):
    conn = sqlite3.connect('movies.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT AVG(rating), COUNT(rating) 
    FROM ratings 
    WHERE movie_id = ?
    ''', (movie_id,))
    avg_rating, votes = cursor.fetchone()
    conn.close()
    
    rating_text = f"⭐ Current Rating: {avg_rating:.1f}/5 ({votes} votes)"
    await context.bot.edit_message_text(
        chat_id=message.chat_id,
        message_id=message.message_id,
        text=f"{rating_text}\n⏳ Auto-deletes in 3 hours\n\n"
             "🎯 Watched it? Rate:",
        reply_markup=message.reply_markup
    )

# Auto-delete messages
async def auto_delete(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.delete_message(job.chat_id, job.data)

# Main message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != PUBLIC_CHANNEL_ID:
        return
    
    query = update.message.text.strip()
    movie = await search_movie(query)
    
    if not movie:
        await update.message.reply_text("🎬 Movie not found. Try exact title!")
        return
    
    # Forward movie from private channel
    forwarded_msg = await context.bot.forward_message(
        chat_id=PUBLIC_CHANNEL_ID,
        from_chat_id=PRIVATE_CHANNEL_ID,
        message_id=movie[1]
    )
    
    # Create rating buttons
    keyboard = [
        [InlineKeyboardButton("⭐", callback_data=f"rate_{movie[0]}_1"),
         InlineKeyboardButton("⭐⭐", callback_data=f"rate_{movie[0]}_2"),
         InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_{movie[0]}_3")],
        [InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_{movie[0]}_4"),
         InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_{movie[0]}_5")]
    ]
    
    rating_msg = await context.bot.send_message(
        chat_id=PUBLIC_CHANNEL_ID,
        text="⭐ Current Rating: New movie!\n⏳ Auto-deletes in 3 hours\n\n"
             "🎯 Watched it? Rate:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # Schedule deletion
    context.job_queue.run_once(
        auto_delete,
        AUTO_DELETE_SECONDS,
        chat_id=PUBLIC_CHANNEL_ID,
        data=forwarded_msg.message_id
    )
    context.job_queue.run_once(
        auto_delete,
        AUTO_DELETE_SECONDS,
        chat_id=PUBLIC_CHANNEL_ID,
        data=rating_msg.message_id
    )

# Index existing movies
async def index_existing_movies(app: Application):
    conn = sqlite3.connect('movies.db')
    cursor = conn.cursor()
    
    async for message in app.bot.get_chat_history(PRIVATE_CHANNEL_ID, limit=100):
        if message.text and "🎬" in message.text:
            title = message.text.split('\n')[0].replace("🎬", "").strip()
            cursor.execute('''
            INSERT OR IGNORE INTO movies (title, private_msg_id)
            VALUES (?, ?)
            ''', (title, message.message_id))
    
    conn.commit()
    conn.close()

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_rating, pattern="^rate_"))
    
    # Index existing movies on startup
    app.add_handler(MessageHandler(filters.ALL, index_existing_movies), group=-1)
    
    app.run_polling()

if __name__ == "__main__":
    main()
