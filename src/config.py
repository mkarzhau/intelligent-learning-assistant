import os
from dotenv import load_dotenv

# Эта функция ищет файл .env в корне проекта и загружает из него переменные
load_dotenv()

# --- Основные переменные, которые использует ваш бот ---

# Токен вашего Telegram бота из @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Ключ для API Google Gemini, который вы получили в Google AI Studio
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Настройки для подключения к MongoDB
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://maksatkarzhaubaev91_db_user:T4mCjrXJv0lHMyGU@cluster0.xuolghj.mongodb.net/?appName=Cluster0")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "intelligent_learning_assistant")
ADMIN_GROUP_ID = -4837118350  # замените на свой chat_id группы
ADMIN_IDS = [1046704546, 5420897726, 1044841557, 965417533]
# --- Проверка на наличие обязательных ключей ---
# Если бот не сможет запуститься без этих ключей, он сразу выдаст понятную ошибку.
if not BOT_TOKEN:
    raise ValueError("Ошибка: BOT_TOKEN не найден. Пожалуйста, добавьте его в .env файл.")

if not GOOGLE_API_KEY:
    raise ValueError("Ошибка: GOOGLE_API_KEY не найден. Пожалуйста, добавьте его в .env файл.")
