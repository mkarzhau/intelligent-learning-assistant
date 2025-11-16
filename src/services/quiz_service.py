import google.generativeai as genai
import json
import logging
import config

# Конфигурируем API ключ при запуске
try:
    genai.configure(api_key=config.GOOGLE_API_KEY)
except Exception as e:
    logging.error(f"Google Gemini API Configuration Error: {e}")
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
        logging.error(f"Error when generating a quiz using Gemini:{e}")
        return None

async def get_generic_answer(question: str) -> str | None:
    """
    Получает короткий и чёткий ответ на вопрос от модели Gemini с HTML-разметкой.
    """
    try:
        prompt = (
            "You are a friendly and intelligent assistant for students. "
            "Give SHORT and precise answers, with a maximum of 5–6 sentences. "
            "If you need to highlight something, use only HTML tags (<b>, <i>, <u>, <code>) instead of Markdown. "
            "Do not use asterisks or underscores for formatting — only HTML tags. "
            "Your answer must be clear and concise. "
            "IMPORTANT: Always reply in ENGLISH, no matter what language the question is asked in.\n\n"
            f"QUESTION: {question}"
        )
        response = await qa_model.generate_content_async(prompt)
        if response.parts:
            return response.text
        else:
            logging.warning("Gemini returned an empty answer to the general question.")
            return "Unfortunately, I couldn't find an answer to your question."
    except Exception as e:
        logging.error(f"Error when receiving a response from Gemini: {e}")
        return "An error occurred while receiving the response."