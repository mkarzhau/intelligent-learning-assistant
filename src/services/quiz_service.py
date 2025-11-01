import google.generativeai as genai
import json
import logging
from .. import config

# Конфигурируем API ключ при запуске
try:
    genai.configure(api_key=config.GOOGLE_API_KEY)
except Exception as e:
    logging.error(f"Ошибка конфигурации Google Gemini API: {e}")
    raise

# Модель для учебных материалов (квизы, конспекты)
quiz_model = genai.GenerativeModel('models/gemini-2.5-pro')
qa_model = genai.GenerativeModel('models/gemini-2.5-flash')

async def generate_quiz_from_text(text_block: str) -> dict | None:
    """
    Отправляет текстовый блок в Gemini и получает структурированный квиз в формате JSON.
    """
    prompt = f"""
    You are an expert educational assistant. Your task is to create study materials from a given text block.
    The output must be a valid JSON object, without any surrounding text or markdown formatting.
    
    For each question, ALWAYS include an "explanation" field with a short explanation (1-2 sentences) why the answer is correct. Do not skip this field.

    TEXT BLOCK:
    ---
    {text_block}
    ---

    REQUIRED JSON OUTPUT STRUCTURE:
    {{
        "summary": "A concise summary of the text in 2-3 sentences.",
        "explanation": "A simplified explanation of the key concepts in 2-3 sentences, suitable for an 18-year-old.",
        "questions": [
            {{
            "type": "mcq",
            "question": "A multiple-choice question based on the text.",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "answer": "The correct option letter (e.g., 'A').",
            "hint": "A short hint for the student if they answer incorrectly.",
            "explanation": "A short explanation why this answer is correct."
            }}
        ]
    }}
    """
    try:
        generation_config = genai.types.GenerationConfig(response_mime_type="application/json")
        response = await quiz_model.generate_content_async(prompt, generation_config=generation_config)
        quiz_data = json.loads(response.text)
        return quiz_data
    except Exception as e:
        logging.error(f"Ошибка при генерации квиза с помощью Gemini: {e}")
        return None

async def get_generic_answer(question: str) -> str | None:
    """
    Получает короткий и чёткий ответ на вопрос от модели Gemini с HTML-разметкой.
    """
    try:
        prompt = (
            "Ты — дружелюбный и умный помощник для студентов. "
            "Отвечай КОРОТКО и по делу, максимум 5-6 предложений. "
            "Если нужно выделить что-то, используй только HTML-теги (<b>, <i>, <u>, <code>), а не Markdown. "
            "Не используй звёздочки или подчёркивания для выделения — только HTML. "
            "Ответ должен быть понятным и лаконичным. "
            "ВНИМАНИЕ: всегда отвечай на том же языке, на котором задан вопрос.\n\n"
            f"ВОПРОС: {question}"
        )
        response = await qa_model.generate_content_async(prompt)
        if response.parts:
            return response.text
        else:
            logging.warning("Gemini вернул пустой ответ на общий вопрос.")
            return "К сожалению, я не смог найти ответ на ваш вопрос."
    except Exception as e:
        logging.error(f"Ошибка при получении ответа от Gemini: {e}")
        return "Произошла ошибка при получении ответа."