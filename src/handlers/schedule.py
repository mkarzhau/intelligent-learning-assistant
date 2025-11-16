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
    logging.info("The daily task of sending training blocks and reminders has been started.")

    async for course in db["courses"].find({"exam_date": {"$exists": True}}):
        user_id = course["user_id"]
        course_title = course.get("title", "Untitled")
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
        reminder_text = f"🗓️ <b>Course Reminder «{course_title}»</b>\n"
        if days_left < 0:
            reminder_text += "\nThe exam has already passed! I hope everything went well. 🎉"
            await bot.send_message(chat_id=user_id, text=reminder_text, parse_mode="HTML")
            continue

        if days_left == 0:
            reminder_text += "\n🔥 <b>Exam is today!</b> Good luck!\n"
        else:
            reminder_text += f"\nDays left until the exam: <b>{days_left} days</b>.\n"

        reminder_text += f"\n📚 Remaining study blocks: <b>{blocks_left}</b>."
        reminder_text += f"\n📖 Lectures uploaded: <b>{lectures_uploaded}</b> out of {expected_lectures}."
        if lectures_needed > 0:
            reminder_text += f"\n⚠️ Missing lectures: <b>{lectures_needed}</b>. Upload them for full preparation!"

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
            summary = block.get('summary', 'No summary available.')
            explanation = block.get('explanation', 'No explanation available.')
            text_to_send = (
                f"🔔 <b>New study material!</b>\n\n"
                f"📚 <b>Course:</b> «{course_title}»\n\n"
                f"<b>Summary:</b>\n<i>{summary}</i>\n\n"
                f"<b>In simple words:</b>\n<i>{explanation}</i>"
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ I've studied", callback_data=f"block_learned:{block['_id']}")]
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
                logging.error(f"Couldn't send the block to the user {user_id}: {e}")