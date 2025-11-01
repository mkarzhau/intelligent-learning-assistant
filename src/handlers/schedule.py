import logging
import math
from aiogram import Bot
from motor.motor_asyncio import AsyncIOMotorDatabase
from .common import now_local, ensure_aware, now_utc
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

async def send_daily_blocks_and_reminder(bot: Bot, db: AsyncIOMotorDatabase):
    """
    Каждый день отправляет пользователю нужное количество блоков по каждому курсу,
    чтобы успеть до экзамена, и присылает подробное напоминание.
    """
    logging.info("Запущена ежедневная задача по отправке учебных блоков и напоминаний.")

    async for course in db["courses"].find({"exam_date": {"$exists": True}}):
        user_id = course["user_id"]
        course_title = course.get("title", "Без названия")
        exam_date = ensure_aware(course["exam_date"])
        expected_lectures = course.get("expected_lectures", 0)

        now = now_local()
        days_left = (exam_date.date() - now.date()).days

        lectures_uploaded = await db["lectures"].count_documents({"course_id": course["_id"]})
        lectures_needed = max(0, expected_lectures - lectures_uploaded)

        blocks_cursor = db["blocks"].find({
            "course_id": course["_id"],
            "user_id": user_id,
            "sent_at": {"$exists": False}
        })
        blocks = [block async for block in blocks_cursor]
        blocks_left = len(blocks)

        # --- Формируем напоминание ---
        reminder_text = f"🗓️ <b>Напоминание по курсу «{course_title}»</b>\n"
        if days_left < 0:
            reminder_text += "\nЭкзамен уже прошёл! Надеюсь, всё прошло успешно. 🎉"
            await bot.send_message(chat_id=user_id, text=reminder_text, parse_mode="HTML")
            continue

        if days_left == 0:
            reminder_text += "\n🔥 <b>Экзамен уже сегодня!</b> Удачи!\n"
        else:
            reminder_text += f"\nДо экзамена осталось: <b>{days_left} дней</b>.\n"

        reminder_text += f"\n📚 Осталось учебных блоков: <b>{blocks_left}</b>."
        reminder_text += f"\n📖 Загружено лекций: <b>{lectures_uploaded}</b> из {expected_lectures}."
        if lectures_needed > 0:
            reminder_text += f"\n⚠️ Не хватает лекций: <b>{lectures_needed}</b>. Загрузите их для полной подготовки!"

        await bot.send_message(chat_id=user_id, text=reminder_text, parse_mode="HTML")

        # --- Отправляем учебные блоки ---
        if blocks_left == 0:
            continue  # Нет новых блоков для отправки

        # Если экзамен сегодня — отправляем все оставшиеся блоки
        if days_left == 0:
            blocks_to_send = blocks
        else:
            # Отправляем столько блоков, чтобы успеть до экзамена
            blocks_per_day = max(1, math.ceil(blocks_left / days_left))
            blocks_to_send = blocks[:blocks_per_day]

        for block in blocks_to_send:
            summary = block.get('summary', 'Нет краткого содержания.')
            explanation = block.get('explanation', 'Нет объяснения.')
            text_to_send = (
                f"🔔 <b>Новый материал для изучения!</b>\n\n"
                f"📚 <b>Курс:</b> «{course_title}»\n\n"
                f"<b>Краткое содержание:</b>\n<i>{summary}</i>\n\n"
                f"<b>Простыми словами:</b>\n<i>{explanation}</i>"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Я изучил", callback_data=f"block_learned:{block['_id']}")]
                ]
            )
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=text_to_send,
                    parse_mode="HTML",
                    reply_markup=keyboard  # <-- вот здесь добавляем клавиатуру!
                )
                await db["blocks"].update_one(
                    {"_id": block["_id"]},
                    {"$set": {"sent_at": now_utc()}}
                )
            except Exception as e:
                logging.error(f"Не удалось отправить блок пользователю {user_id}: {e}")