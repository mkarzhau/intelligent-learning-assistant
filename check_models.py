# filepath: d:\RMT\intelligent-learning-assistant\check_models.py
import google.generativeai as genai
from dotenv import load_dotenv
import os

# Загружаем ключ из вашего .env файла
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("Ошибка: GOOGLE_API_KEY не найден в файле .env")
else:
    try:
        genai.configure(api_key=api_key)
        print("--- Список моделей, доступных вашему ключу ---")
        for m in genai.list_models():
            # Проверяем, что модель поддерживает метод 'generateContent' (генерацию текста)
            if 'generateContent' in m.supported_generation_methods:
                print(m.name)
        print("-------------------------------------------------")
        print("\nСкопируйте одно из названий выше (например, 'models/gemini-1.0-pro') и вставьте его в quiz_service.py")
    except Exception as e:
        print(f"Произошла ошибка при подключении к Google API: {e}")
