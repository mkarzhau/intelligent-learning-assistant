from email.mime import message
import io
import logging
import datetime
import hashlib
import pdfplumber
import asyncio
import math
from docx import Document
from aiogram import F, Router, types, Bot
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
import csv
from ..config import ADMIN_IDS
from ..config import ADMIN_GROUP_ID
from ..services import quiz_service
from zoneinfo import ZoneInfo
from aiogram.types import BufferedInputFile
import time
import pandas as pd
import io
import matplotlib.pyplot as plt

async def is_admin(user_id, db):
    user = await db["users"].find_one({"telegram_id": user_id})
    return user and user.get("role") == "admin"

async def log_admin_action(db: AsyncIOMotorDatabase, admin_id: int, action: str, details: dict = None):
    await db["admin_logs"].insert_one({
        "admin_id": admin_id,
        "action": action,
        "details": details or {},
        "timestamp": datetime.datetime.utcnow()
    })

async def notify_admins(bot: Bot, text: str):
    from config import ADMIN_GROUP_ID
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text)
  
async def is_blocked(user_id, db):
    user = await db["users"].find_one({"telegram_id": user_id})
    return user and user.get("blocked", False)  

async def generate_daily_motivation():
    prompt = (
        "Сгенерируй короткое мотивационное сообщение для студента, чтобы вдохновить его на учебу сегодня. "
        "Сообщение должно быть позитивным, на русском языке, не длиннее 2-х предложений."
    )
    # Используй свой сервис для генерации текста
    motivation = await quiz_service.get_generic_answer(prompt)
    return motivation.strip()

async def send_motivation_to_all(bot: Bot, db: AsyncIOMotorDatabase):
    users = await db["users"].find({"blocked": {"$ne": True}}).to_list(length=10000)
    text = await generate_daily_motivation()
    for user in users:
        try:
            await bot.send_message(chat_id=user["telegram_id"], text=text)
        except Exception:
            continue

router = Router()

LOCAL_TZ = ZoneInfo("Asia/Almaty")

UTC = ZoneInfo("UTC")

def now_utc() -> datetime.datetime:
    return datetime.datetime.now(tz=UTC)

def now_local() -> datetime.datetime:
    """Текущее локальное время (aware datetime в Asia/Almaty)."""
    return datetime.datetime.now(tz=LOCAL_TZ)

def parse_date(text: str) -> datetime.datetime | None:
    """
    Парсит строку 'ДД.MM.ГГГГ' и возвращает timezone-aware datetime в LOCAL_TZ (00:00 локального дня).
    """
    try:
        dt = datetime.datetime.strptime(text.strip(), "%d.%m.%Y")
        return datetime.datetime(dt.year, dt.month, dt.day, 0, 0, tzinfo=LOCAL_TZ)
    except Exception:
        return None


def academic_week(start_dt: datetime.datetime, now_dt: datetime.datetime | None = None) -> int:
    if now_dt is None:
        now_dt = now_local()
    # Приводим обе даты к date() в локальной зоне
    start_date = start_dt.astimezone(LOCAL_TZ).date()
    now_date = now_dt.astimezone(LOCAL_TZ).date()
    days = (now_date - start_date).days
    if days < 0:
        return 0
    return days // 7 + 1

def exam_phase_info(start_dt: datetime.datetime, now_dt: datetime.datetime | None = None) -> dict:
    """
    Возвращает словарь с текущей неделей и какие экзамены уже прошли/скоро.
    mid = week 5, end = week 10, final = weeks 11-12
    """
    now_dt = now_dt or now_local()
    week = academic_week(start_dt, now_dt)
    info = {"current_week": week, "passed": [], "upcoming": []}
    if week >= 5:
        info["passed"].append("mid")
    else:
        info["upcoming"].append("mid")
    if week >= 10:
        info["passed"].append("end")
    else:
        info["upcoming"].append("end")
    if week >= 11:
        info["passed"].append("final")
    else:
        info["upcoming"].append("final")
    return info

# --- Состояния FSM ---
class AddCourse(StatesGroup):
    waiting_for_title = State()
    waiting_for_start_date = State()
    waiting_for_exam_type = State()
    waiting_for_date = State()
    waiting_for_lecture_count = State()
    waiting_for_notification_period = State()

class FileUpload(StatesGroup):
    waiting_for_course_choice = State()
    
class LectureQuiz(StatesGroup):
    answering = State()

@router.message(Command("set_admin"))
async def set_admin(message: Message, db: AsyncIOMotorDatabase):
    parts = message.text.split(maxsplit=2)
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    if len(parts) < 2:
        await message.answer("Используйте: /set_admin <user_id>")
        return
    user_id = int(parts[1])
    await db["users"].update_one({"telegram_id": user_id}, {"$set": {"role": "admin"}})
    await log_admin_action(db, message.from_user.id, "set_admin", {"set_admin_id": user_id})
    await message.answer(f"Пользователь {user_id} назначен админом.")

# --- Хендлеры для команды /add_course ---
@router.message(Command("help"))
async def help_command(message: Message, db: AsyncIOMotorDatabase):
    is_admin_user = await is_admin(message.from_user.id, db)
    text = (
        "<b>Доступные команды:</b>\n\n"
        "/add_course — добавить новый курс\n"
        "/delete_course — удалить курс\n"
        "/edit_course — изменить название курса\n"
        "/show_courses — показать ваши курсы и статус блоков\n"
        "/my_files — список загруженных файлов\n"
        "/delete_file — удалить загруженный файл\n"
        "/preview_schedule — предпросмотр расписания отправки блоков\n"
        "/simulate_send — симуляция отправки ближайших блоков (тест)\n"
        "/pause_notifications — отключить уведомления\n"
        "/resume_notifications — включить уведомления\n"
        "/search \"слово\" — поиск по материалам\n"
        "/export — экспортировать учебные блоки (.csv)\n"
        "/profile — ваш прогресс, очки и достижения\n"
        "/top — рейтинг учащихся\n"
        "/feedback — отправить отзыв\n"
        "/start — приветствие и инструкция\n"
        "/help — показать эту справку\n"
    )
    if is_admin_user:
        text += (
            "\n<b>Админ-команды:</b>\n"
            "/set_admin \"User ID\" — назначить админа\n"
            "/block_user — заблокировать пользователя\n"
            "/admin_report — отчет по пользователям\n"
            "/admin_load — нагрузка системы\n"
            "/admin_message — отправить сообщение пользователю или всем\n"
            "/unblock_user — разблокировать пользователя\n"
            "/get_id — узнать chat_id группы\n"
        )
    text += (
        "\n<b>Геймификация и квизы:</b>\n"
        "- После изучения всех блоков лекции появится квиз.\n"
        "- За правильные ответы начисляются очки.\n"
        "- За 100% правильных — бейдж «Квиз-мастер».\n"
        "- Ваши очки и достижения — в /profile.\n"
        "- Рейтинг — в /top.\n"
        "- После квиза показываются ошибки и пояснения.\n"
        "- Просто напишите вопрос — и я помогу с учебой!"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("add_course"))
async def add_course_start(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    course_count = await db["courses"].count_documents({"user_id": message.from_user.id})
    if course_count >= 10:
        await message.answer("❌ Вы достигли максимального количества курсов (10). Удалите старый курс, чтобы добавить новый.")
        return
    await state.set_state(AddCourse.waiting_for_title)
    await message.answer("Введите название нового курса (например, 'История Древнего мира').")

@router.message(Command("simulate_send"))
async def simulate_send_start(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=20)
    if not user_courses:
        await message.answer("У вас нет добавленных курсов.")
        return
    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(text=course['title'], callback_data=f"simulate_send:{course['_id']}"))
    builder.adjust(1)
    await message.answer("Выберите курс для симуляции отправки (отправятся ближайшие блоки):", reply_markup=builder.as_markup())

@router.message(Command("block_user"))
async def block_user_start(message: Message, db: AsyncIOMotorDatabase):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    users = await db["users"].find({"role": {"$ne": "admin"}, "blocked": {"$ne": True}}).to_list(length=100)
    if not users:
        await message.answer("Нет доступных пользователей для блокировки.")
        return
    builder = InlineKeyboardBuilder()
    for user in users:
        name = user.get("name", "Без имени")
        uid = user.get("telegram_id")
        builder.add(InlineKeyboardButton(
            text=f"{name} ({uid})",
            callback_data=f"block_user_select:{uid}"
        ))
    builder.adjust(1)
    await message.answer("Выберите пользователя для блокировки:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("block_user_select:"))
async def block_user_confirm(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    user_id = int(callback.data.split(":")[1])
    user = await db["users"].find_one({"telegram_id": user_id})
    if not user:
        await callback.message.answer("Пользователь не найден.")
        return
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="Да, заблокировать", callback_data=f"block_user_confirm:{user_id}"),
        InlineKeyboardButton(text="Нет", callback_data="cancel_block_user")
    )
    await callback.message.answer(
        f"Вы уверены, что хотите заблокировать пользователя:\n"
        f"{user.get('name', 'Без имени')} (ID: {user_id})?",
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data.startswith("block_user_confirm:"))
async def block_user_real(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    user_id = int(callback.data.split(":")[1])
    await db["users"].update_one({"telegram_id": user_id}, {"$set": {"blocked": True}})
    await log_admin_action(db, callback.from_user.id, "block_user", {"blocked_id": user_id})
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Пользователь {user_id} успешно заблокирован.")

@router.callback_query(F.data == "cancel_block_user")
async def cancel_block_user(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Блокировка отменена.")

@router.message(Command("feedback"))
async def feedback_start(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    await state.set_state("waiting_for_feedback")
    await message.answer("Напишите ваш отзыв или предложение:")

@router.message(Command("admin_message"))
async def admin_message_start(message: Message, db: AsyncIOMotorDatabase):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    users = await db["users"].find({"blocked": {"$ne": True}}).to_list(length=100)
    builder = InlineKeyboardBuilder()
    for user in users:
        name = user.get("name", "Без имени")
        uid = user.get("telegram_id")
        builder.add(InlineKeyboardButton(
            text=f"{name} ({uid})",
            callback_data=f"admin_msg_select:{uid}"
        ))
    builder.add(InlineKeyboardButton(
        text="Всем пользователям", callback_data="admin_msg_select:all"
    ))
    builder.adjust(1)
    await message.answer("Кому отправить сообщение?", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_msg_select:"))
async def admin_message_text(callback: CallbackQuery, state: FSMContext):
    target = callback.data.split(":")[1]
    await state.update_data(admin_msg_target=target)
    await callback.message.answer("Введите текст сообщения для отправки:")
    await state.set_state("waiting_for_admin_msg_text")

@router.message(StateFilter("waiting_for_admin_msg_text"))
async def admin_message_send(message: Message, state: FSMContext, db: AsyncIOMotorDatabase, bot: Bot):
    data = await state.get_data()
    target = data.get("admin_msg_target")
    text = message.text.strip()
    if target == "all":
        users = await db["users"].find({"blocked": {"$ne": True}}).to_list(length=1000)
        count = 0
        for user in users:
            try:
                await bot.send_message(chat_id=user["telegram_id"], text=text)
                count += 1
            except Exception:
                continue
        await message.answer(f"Сообщение отправлено {count} пользователям.")
    else:
        try:
            await bot.send_message(chat_id=int(target), text=text)
            await message.answer("Сообщение отправлено выбранному пользователю.")
        except Exception as e:
            await message.answer(f"Ошибка отправки: {e}")
    await state.clear()

@router.message(StateFilter("waiting_for_feedback"))
async def feedback_receive(message: Message, state: FSMContext, db: AsyncIOMotorDatabase, bot: Bot):
    await db["feedback"].insert_one({
        "user_id": message.from_user.id,
        "text": message.text,
        "created_at": datetime.datetime.utcnow()
    })
    await message.answer("Спасибо за ваш отзыв!")
    await state.clear()

    # Отправка фидбэка в группу админов
    feedback_text = (
        f"📩 Новый фидбэк!\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Telegram ID: {message.from_user.id}\n"
        f"Время: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Текст: {message.text}"
    )
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=feedback_text)

@router.message(Command("export"))
async def export_choose_course(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=20)
    if not user_courses:
        await message.answer("У вас нет добавленных курсов.")
        return
    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(text=course['title'], callback_data=f"export_course:{course['_id']}"))
    builder.adjust(1)
    await message.answer("Выберите курс для экспорта учебных блоков:", reply_markup=builder.as_markup())

@router.message(Command("admin_logs"))
async def admin_logs(message: Message, db: AsyncIOMotorDatabase):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    logs = await db["admin_logs"].find().sort("timestamp", -1).limit(20).to_list(length=20)
    if not logs:
        await message.answer("Лог действий админов пуст.")
        return
    lines = []
    for log in logs:
        admin_id = log.get("admin_id")
        action = log.get("action")
        timestamp = log.get("timestamp").strftime("%Y-%m-%d %H:%M:%S")
        details = log.get("details", {})
        lines.append(f"{timestamp}: admin {admin_id} — {action} — {details}")
    await message.answer("\n".join(lines))

@router.message(Command("admin_stats"))
async def admin_stats(message: Message, db: AsyncIOMotorDatabase, bot: Bot):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return

    # Пример: активность пользователей по дням (можно заменить на свою аналитику)
    pipeline = [
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    feedback_stats = await db["feedback"].aggregate(pipeline).to_list(length=100)
    dates = [item["_id"] for item in feedback_stats]
    counts = [item["count"] for item in feedback_stats]

    plt.figure(figsize=(8, 4))
    plt.bar(dates, counts)
    plt.xticks(rotation=45)
    plt.title("Активность фидбэка пользователей по дням")
    plt.xlabel("Дата")
    plt.ylabel("Количество фидбэков")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    await bot.send_photo(chat_id=message.chat.id, photo=buf, caption="График активности пользователей (фидбэк)")

@router.message(Command("admin_export"))
async def admin_export(message: Message, db: AsyncIOMotorDatabase, bot: Bot):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return

    # Получаем пользователей
    users = await db["users"].find().to_list(length=1000)
    # Получаем курсы
    courses = await db["courses"].find().to_list(length=1000)
    # Получаем прогресс
    progress = await db["progress"].find().to_list(length=1000)

    # Формируем DataFrame
    user_data = []
    for user in users:
        user_courses = [c for c in courses if c["user_id"] == user["id"]]
        user_progress = [p for p in progress if p["user_id"] == user["id"]]
        for course in user_courses:
            course_progress = next((p for p in user_progress if p["course_id"] == course["id"]), None)
            user_data.append({
                "User ID": user["id"],
                "Username": user.get("username", ""),
                "Role": user.get("role", ""),
                "Course": course.get("name", ""),
                "Completed Blocks": course_progress["completed_blocks"] if course_progress else 0,
                "Total Blocks": course_progress["total_blocks"] if course_progress else 0
            })

    df = pd.DataFrame(user_data)
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    # Отправляем CSV-файл админу
    await bot.send_document(
        chat_id=message.chat.id,
        document=types.input_file.InputFile(io.BytesIO(output.getvalue().encode()), filename="user_statistics.csv"),
        caption="Экспорт статистики пользователей"
    )

@router.callback_query(F.data.startswith("export_course:"))
async def export_blocks_for_course(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    if await is_blocked(callback.from_user.id, db):
        await callback.message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    course_id = ObjectId(callback.data.split(":")[1])
    blocks = await db["blocks"].find({"course_id": course_id, "user_id": callback.from_user.id}).to_list(length=100)
    if not blocks:
        await callback.message.answer("Нет блоков для этого курса.")
        return
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["summary", "explanation"])
    for block in blocks:
        writer.writerow([block.get("summary", ""), block.get("explanation", "")])
    output.seek(0)
    await callback.message.answer_document(
        BufferedInputFile(output.getvalue().encode(), filename="export.csv")
    )
    await callback.message.edit_reply_markup(reply_markup=None)
#     await message.answer("Название сохранено. Теперь введите дату экзамена в формате ДД.ММ.ГГГГ.")

# ...existing code...

@router.message(AddCourse.waiting_for_date)
async def add_course_date(message: Message, state: FSMContext):
    exam_dt = parse_date(message.text)
    if not exam_dt:
        await message.answer("❌ Неверный формат даты. Пожалуйста, введите дату в формате ДД.MM.ГГГГ.")
        return

    if exam_dt.year != 2025:
        await message.answer("❌ Экзамен должен быть в 2025 году. Введите корректную дату.")
        return

    now = now_local()
    if exam_dt < now:
        await message.answer("❌ Дата экзамена уже прошла. Введите будущую дату экзамена.")
        return

    data = await state.get_data()
    start_dt = data.get("start_date")
    if not start_dt:
        await message.answer("❌ Внутренняя ошибка: не найдена дата начала. Пожалуйста, начните /add_course заново.")
        await state.clear()
        return

    if exam_dt < start_dt:
        await message.answer("❌ Дата экзамена не может быть раньше даты начала занятий. Введите корректную дату экзамена.")
        return

    # Сохраняем дату экзамена
    await state.update_data(exam_date=exam_dt)

    # Информируем пользователя о текущей академической неделе и фазах
    info = exam_phase_info(start_dt, now_local())
    week = info["current_week"]
    passed = ", ".join(info["passed"]) if info["passed"] else "ничего"
    upcoming = ", ".join(info["upcoming"]) if info["upcoming"] else "ничего"
    await message.answer(
        f"Дата экзамена принята.\n"
        f"С начала занятий прошло: <b>{week}</b> нед(и).\n"
        f"Пройдено: {passed}.\n"
        f"Скоро: {upcoming}.\n\n"
        "Далее укажите, сколько всего лекций планируется в курсе (целое число)."
        , parse_mode="HTML"
    )

    await state.set_state(AddCourse.waiting_for_lecture_count)
    
# ...existing code...

@router.message(AddCourse.waiting_for_lecture_count)
async def add_course_lectures(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Пожалуйста, введите число.")
        return

    num = int(message.text)
    data = await state.get_data()
    exam_dt = data.get("exam_date")
    start_dt = data.get("start_date")
    # Рекомендации: mid/end/final
    rec = "Рекомендация: для mid/end обычно 5 лекций, для final — 10 (включая предыдущие)."
    await state.update_data(expected_lectures=num)
    await state.set_state(AddCourse.waiting_for_notification_period)
    await message.answer(
        f"Количество лекций сохранено: {num}.\n{rec}\n\n"
        "В какое время суток тебе удобно получать учебные сообщения?\n"
        "Выбери: утро, день или вечер."
    )

class EditCourse(StatesGroup):
    waiting_for_course_choice = State()
    waiting_for_new_title = State()

@router.message(Command("edit_course"))
async def edit_course_start(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=10)
    if not user_courses:
        await message.answer("У вас нет добавленных курсов.")
        return

    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(
            text=course['title'],
            callback_data=f"edit_course:{course['_id']}"
        ))
    builder.adjust(1)
    await state.set_state(EditCourse.waiting_for_course_choice)
    await message.answer("Выберите курс для изменения названия:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("edit_course:"), EditCourse.waiting_for_course_choice)
async def edit_course_choose(callback: CallbackQuery, state: FSMContext, db: AsyncIOMotorDatabase):
    if await is_blocked(callback.from_user.id, db):
        await callback.message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    course_id = callback.data.split(":")[1]
    await state.update_data(course_id=course_id)
    await state.set_state(EditCourse.waiting_for_new_title)
    await callback.message.answer("Введите новое название курса:")

@router.message(EditCourse.waiting_for_new_title)
async def edit_course_title(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    data = await state.get_data()
    course_id = ObjectId(data["course_id"])
    new_title = message.text.strip()
    await db["courses"].update_one({"_id": course_id}, {"$set": {"title": new_title}})
    await message.answer(f"Название курса успешно обновлено на «{new_title}».")
    await state.clear()

@router.message(Command("my_files"))
async def choose_course_for_files(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=20)
    if not user_courses:
        await message.answer("У вас нет добавленных курсов.")
        return

    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(
            text=course['title'],
            callback_data=f"show_files:{course['_id']}"
        ))
    builder.adjust(1)
    await message.answer("Выберите курс для просмотра файлов:", reply_markup=builder.as_markup())
    
@router.message(Command("delete_file"))
async def delete_file_start(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    lectures = await db["lectures"].find({"user_id": message.from_user.id}).to_list(length=20)
    if not lectures:
        await message.answer("Вы не загружали ни одного файла.")
        return
    builder = InlineKeyboardBuilder()
    for lec in lectures:
        builder.add(InlineKeyboardButton(
            text=lec['filename'],
            callback_data=f"delete_file:{lec['_id']}"
        ))
    builder.adjust(1)
    await message.answer("Выберите файл для удаления:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("delete_file:"))
async def confirm_delete_file(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    if await is_blocked(callback.from_user.id, db):
        await callback.message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    file_id = ObjectId(callback.data.split(":")[1])
    file = await db["lectures"].find_one({"_id": file_id})
    if not file:
        await callback.message.answer("Файл не найден.")
        return
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="Да, удалить", callback_data=f"confirm_delete_file:{file_id}"),
        InlineKeyboardButton(text="Нет", callback_data="cancel_delete_file")
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Вы уверены, что хотите удалить файл «{file['filename']}»?", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("confirm_delete_file:"))
async def really_delete_file(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    file_id = ObjectId(callback.data.split(":")[1])
    file = await db["lectures"].find_one({"_id": file_id})
    await db["lectures"].delete_one({"_id": file_id})
    await db["blocks"].delete_many({"lecture_id": file_id})
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Файл «{file['filename']}» и связанные учебные блоки удалены.")

@router.callback_query(F.data == "cancel_delete_file")
async def cancel_delete_file(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Удаление файла отменено.")

@router.message(Command("search"))
async def search_blocks(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Введите ключевое слово для поиска после /search.")
        return
    results = await db["blocks"].find({"$text": {"$search": query}, "user_id": message.from_user.id}).to_list(length=10)
    if not results:
        await message.answer("Ничего не найдено.")
        return
    for block in results:
        await message.answer(f"Найдено:\n{block.get('summary', '')}\n{block.get('explanation', '')}")

@router.message(AddCourse.waiting_for_notification_period)
async def add_course_notification_period(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    period = message.text.strip().lower()
    if period not in ["утро", "день", "вечер"]:
        await message.answer("❌ Введи только: утро, день или вечер.")
        return

    user_data = await state.get_data()
    # Преобразуем локальные даты в UTC для хранения
    start_dt_local = user_data.get('start_date')
    exam_dt_local = user_data.get('exam_date')
    start_dt_utc = start_dt_local.astimezone(ZoneInfo("UTC")) if start_dt_local else None
    exam_dt_utc = exam_dt_local.astimezone(ZoneInfo("UTC")) if exam_dt_local else None

    await db["courses"].insert_one({
        "user_id": message.from_user.id,
        "title": user_data['title'],
        "start_date": start_dt_utc,
        "exam_date": exam_dt_utc,
        "exam_type": user_data.get("exam_type"),
        "expected_lectures": user_data.get('expected_lectures'),
        "notification_period": period,
        "created_at": datetime.datetime.utcnow()
    })
    await message.answer(
        f"✅ Курс «{user_data['title']}» успешно добавлен!\n\n"
        "Теперь вы будете получать учебные сообщения в выбранный период суток."
    )
    await state.clear()

# --- Хендлеры для "Умной загрузки" файлов ---

@router.message(F.document)
async def handle_document_start(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=10)

    if not user_courses:
        await message.answer("❌ Вы еще не добавили ни одного курса. Сначала добавьте курс с помощью /add_course.")
        return

    await state.update_data(
        file_id=message.document.file_id,
        file_name=message.document.file_name
    )

    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(
            text=course['title'],
            callback_data=f"select_course:{course['_id']}"
        ))
    builder.adjust(1)

    await state.set_state(FileUpload.waiting_for_course_choice)
    await message.answer("К какому курсу относится этот файл?", reply_markup=builder.as_markup())


    
@router.message(Command("profile"))
async def show_profile(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user = await db["users"].find_one({"telegram_id": message.from_user.id})
    score = user.get("score", 0) if user else 0
    badges = user.get("badges", []) if user else []
    text = (
        f"👤 <b>Профиль</b>\n"
        f"Очки: <b>{score}</b>\n"
        f"Достижения: {', '.join(badges) if badges else 'Пока нет'}"
    )
    await message.answer(text, parse_mode="HTML")    

@router.message(Command("top"))
async def show_top_users(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    top_users = await db["users"].find().sort("score", -1).limit(10).to_list(length=10)
    if not top_users:
        await message.answer("Таблица лидеров пуста.")
        return
    lines = []
    for idx, user in enumerate(top_users, start=1):
        name = user.get("name", "Без имени")
        score = user.get("score", 0)
        lines.append(f"{idx}. {name} — {score} очков")
    await message.answer("<b>🏆 Топ-10 учащихся:</b>\n" + "\n".join(lines), parse_mode="HTML")
 
@router.message(Command("admin_report"))
async def admin_report(message: Message, db: AsyncIOMotorDatabase):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    users = await db["users"].find().to_list(length=100)
    report = []
    for user in users:
        courses = await db["courses"].count_documents({"user_id": user["telegram_id"]})
        files = await db["lectures"].count_documents({"user_id": user["telegram_id"]})
        score = user.get("score", 0)
        report.append(f"{user.get('name', 'Без имени')} (ID: {user['telegram_id']}): курсов={courses}, файлов={files}, очки={score}")
    await message.answer("\n".join(report))

@router.message(Command("admin_load"))
async def admin_load(message: Message, db: AsyncIOMotorDatabase):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    users_count = await db["users"].count_documents({})
    courses_count = await db["courses"].count_documents({})
    lectures_count = await db["lectures"].count_documents({})
    blocks_count = await db["blocks"].count_documents({})
    qa_count = await db["qa_history"].count_documents({})
    feedback_count = await db["feedback"].count_documents({})
    text = (
        "<b>📊 Нагрузка системы:</b>\n"
        f"Пользователей: <b>{users_count}</b>\n"
        f"Курсов: <b>{courses_count}</b>\n"
        f"Лекций: <b>{lectures_count}</b>\n"
        f"Блоков: <b>{blocks_count}</b>\n"
        f"Вопросов-ответов: <b>{qa_count}</b>\n"
        f"Фидбэков: <b>{feedback_count}</b>\n"
    )
    await message.answer(text, parse_mode="HTML")
  
@router.callback_query(F.data.startswith("quiz_answer:"), LectureQuiz.answering)
async def quiz_answer_callback(callback: CallbackQuery, state: FSMContext, db: AsyncIOMotorDatabase):
    parts = callback.data.split(":")
    idx = int(parts[1])
    user_choice = int(parts[2])
    data = await state.get_data()
    questions = data["quiz_questions"]
    q = questions[idx]
    # --- Исправление: поддержка строковых ответов ("B") ---
    correct_answer = q["answer"]
    if isinstance(correct_answer, str):
        correct_answer_idx = ord(correct_answer.upper()) - 65
    else:
        correct_answer_idx = correct_answer
    correct = (user_choice == correct_answer_idx)
    answers = data.get("quiz_answers", [])
    answers.append({
        "question": q["question"],
        "user_choice": user_choice,
        "correct_choice": correct_answer,
        "options": q["options"],
        "is_correct": correct,
        "explanation": q.get("explanation", "")
    })
    await state.update_data(
        quiz_current=idx+1,
        quiz_correct=data.get("quiz_correct", 0) + (1 if correct else 0),
        quiz_answers=answers
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_next_quiz_question(callback.message, state, db)
    
async def show_quiz_result(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    data = await state.get_data()
    total = len(data["quiz_questions"])
    correct = data.get("quiz_correct", 0)
    answers = data.get("quiz_answers", [])
    user_id = message.from_user.id

    points = correct * 10
    # Гарантируем, что профиль есть и поле score существует
    await db["users"].update_one(
        {"telegram_id": user_id},
        {"$setOnInsert": {"name": message.from_user.full_name, "score": 0, "badges": []}},
        upsert=True
    )
    # Начисляем очки
    await db["users"].update_one(
        {"telegram_id": user_id},
        {"$inc": {"score": points}}
    )
    if correct == total and total > 0:
        await db["users"].update_one(
            {"telegram_id": user_id},
            {"$addToSet": {"badges": "Квиз-мастер"}}
        )
        badge_text = "\n🏆 <b>Достижение: Квиз-мастер!</b>"
    else:
        badge_text = ""

    # ...остальной код...

    # ...остальной код...

    text = f"📝 <b>Квиз завершён!</b>\n\nПравильных ответов: <b>{correct} из {total}</b>\n"
    text += f"🏅 Получено очков: <b>{points}</b>\n"
    if correct == total:
        text += "🎉 Отлично! Все ответы верны!"
    else:
        text += "\nОшибки:\n"
        for idx, ans in enumerate(answers):
            if not ans["is_correct"]:
                # --- Исправление: поддержка строковых ответов ("C") ---
                user_choice = ans["user_choice"]
                correct_choice = ans["correct_choice"]
                # если строка — переводим в индекс
                if isinstance(user_choice, str):
                    user_choice_idx = ord(user_choice.upper()) - 65
                else:
                    user_choice_idx = user_choice
                if isinstance(correct_choice, str):
                    correct_choice_idx = ord(correct_choice.upper()) - 65
                else:
                    correct_choice_idx = correct_choice
                user_opt = chr(65 + user_choice_idx)
                correct_opt = chr(65 + correct_choice_idx)
                text += (
                    f"\n<b>Вопрос {idx+1}:</b> {ans['question']}\n"
                    f"Твой ответ: {user_opt}. {ans['options'][user_choice_idx]}\n"
                    f"Правильный ответ: {correct_opt}. {ans['options'][correct_choice_idx]}\n"
                    f"Пояснение: {ans['explanation']}\n"
                )
    text += badge_text
    await message.answer(text, parse_mode="HTML")

@router.message(Command("unblock_user"))
async def unblock_user_start(message: Message, db: AsyncIOMotorDatabase):
    if not await is_admin(message.from_user.id, db):
        await message.answer("❌ Только администраторы могут использовать эту команду.")
        return
    users = await db["users"].find({"blocked": True}).to_list(length=100)
    if not users:
        await message.answer("Нет заблокированных пользователей.")
        return
    builder = InlineKeyboardBuilder()
    for user in users:
        name = user.get("name", "Без имени")
        uid = user.get("telegram_id")
        builder.add(InlineKeyboardButton(
            text=f"{name} ({uid})",
            callback_data=f"unblock_user_select:{uid}"
        ))
    builder.adjust(1)
    await message.answer("Выберите пользователя для разблокировки:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("unblock_user_select:"))
async def unblock_user_confirm(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    user_id = int(callback.data.split(":")[1])
    await db["users"].update_one({"telegram_id": user_id}, {"$set": {"blocked": False}})
    await log_admin_action(db, callback.from_user.id, "unblock_user", {"unblocked_id": user_id})
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Пользователь {user_id} успешно разблокирован.")

# Отправь команду /get_id в группе, бот должен обработать её:
@router.message(Command("get_id"))
async def get_group_id(message: Message):
    await message.answer(f"Chat ID этой группы: <code>{message.chat.id}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("select_course:"), FileUpload.waiting_for_course_choice)
async def handle_course_selection(callback: CallbackQuery, state: FSMContext, bot: Bot, db: AsyncIOMotorDatabase):
    await callback.message.edit_text("Отлично! Начинаю проверку и обработку файла... 🤖")
    
    course_id = ObjectId(callback.data.split(":")[1])
    user_data = await state.get_data()
    file_id = user_data['file_id']
    file_name = user_data['file_name']
    
    await state.clear()

    file_info = await bot.get_file(file_id)
    file_in_memory = io.BytesIO()
    await bot.download_file(file_info.file_path, destination=file_in_memory)
    
    file_hash = hashlib.sha256(file_in_memory.getvalue()).hexdigest()
    existing_lecture = await db["lectures"].find_one({"course_id": course_id, "file_hash": file_hash})
    if existing_lecture:
        await callback.message.answer(f"⚠️ Этот файл уже был загружен для этого курса как '{existing_lecture['filename']}'. Обработка отменена.")
        return

    lecture_doc = await db["lectures"].insert_one({
        "course_id": course_id,
        "user_id": callback.from_user.id,
        "filename": file_name,
        "file_hash": file_hash,
        "tg_file_id": file_id,
        "uploaded_at": datetime.datetime.utcnow()
    })
    lecture_id = lecture_doc.inserted_id

    file_in_memory.seek(0)
    text = ""
    try:
        if file_name.lower().endswith('.pdf'):
            with pdfplumber.open(file_in_memory) as pdf:
                text = "".join(page.extract_text() for page in pdf.pages if page.extract_text())
        elif file_name.lower().endswith('.docx'):
            doc = Document(file_in_memory)
            text = "\n".join(para.text for para in doc.paragraphs)
    except Exception as e:
        logging.error(f"Ошибка извлечения текста: {e}")
        await callback.message.answer("❌ Ошибка при чтении файла.")
        return

    if not text.strip():
        await callback.message.answer("❌ Не удалось извлечь текст из файла.")
        return

    words = text.split()
    chunk_size = 350
    text_chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    
    DELAY_BETWEEN_REQUESTS_SEC = 30
    num_chunks = len(text_chunks)
    total_seconds = num_chunks * DELAY_BETWEEN_REQUESTS_SEC
    
    if total_seconds < 60:
        time_str = "меньше минуты"
    else:
        estimated_minutes = math.ceil(total_seconds / 60)
        time_str = f"около {estimated_minutes} мин."

    await callback.message.answer(
        f"Файл разбит на {num_chunks} блоков. Начинаю генерацию квизов.\n"
        f"⏳ <b>Примерное время обработки: {time_str}</b>\n\n"
        f"Я сообщу, когда все будет готово. Можете пока заняться другими делами."
    )

    processed_blocks = 0
    last_notify_time = time.time()
    notify_interval = 300 
    for i, chunk in enumerate(text_chunks):
        for attempt in range(3):  # до 3 попыток для каждого блока
            quiz_data = await quiz_service.generate_quiz_from_text(chunk)
            if quiz_data:
                await db["blocks"].insert_one({
                    "user_id": callback.from_user.id,
                    "course_id": course_id,
                    "lecture_id": lecture_id,
                    "block_index": i + 1,
                    "text": chunk,
                    "summary": quiz_data.get("summary"),
                    "explanation": quiz_data.get("explanation"),
                    "questions": quiz_data.get("questions"),
                })
                processed_blocks += 1
                break  # выйти из попыток, перейти к следующему блоку
            else:
                await asyncio.sleep(30)
            if time.time() - last_notify_time > notify_interval:
                await callback.message.answer(
                    f"⏳ Обработка продолжается...\n"
                    f"Готово {processed_blocks} из {num_chunks} блоков.\n"
                    "Извините за задержку — возможно, превышен лимит запросов к ИИ. Пожалуйста, подождите ещё немного."
                )
                last_notify_time = time.time()
        if i < num_chunks - 1:
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS_SEC)

    # После обработки всех блоков — считаем реально созданные
    processed_blocks_db = await db["blocks"].count_documents({"lecture_id": lecture_id})
    await callback.message.answer(f"✅ Готово! Создано {processed_blocks_db} учебных материалов для курса.")
    
@router.message(AddCourse.waiting_for_title)
async def add_course_title(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    title = message.text.strip()
    # Проверяем, есть ли уже такой курс у пользователя
    existing = await db["courses"].find_one({"user_id": message.from_user.id, "title": title})
    if existing:
        await message.answer("❌ Такой курс уже существует. Введите другое название.")
        return
    await state.update_data(title=title)
    await state.set_state(AddCourse.waiting_for_start_date)
    await message.answer("Введите дату начала занятий в формате ДД.MM.ГГГГ (например, 08.09.2025). Дата должна быть в 2025 году.")

# ...existing code...

@router.message(AddCourse.waiting_for_start_date)
async def add_course_start_date(message: Message, state: FSMContext):
    start_dt = parse_date(message.text)
    if not start_dt:
        await message.answer("❌ Неверный формат даты. Введите дату начала в формате ДД.MM.ГГГГ.")
        return
    if start_dt.year != 2025:
        await message.answer("❌ Дата начала занятий должна быть в 2025 году. Введите корректную дату.")
        return
    await state.update_data(start_date=start_dt)
    await state.set_state(AddCourse.waiting_for_exam_type)

    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="Мидка (5 неделя)", callback_data="exam_type:мидка"),
        InlineKeyboardButton(text="Эндка (10 неделя)", callback_data="exam_type:эндка"),
        InlineKeyboardButton(text="Файнал (11-12 неделя)", callback_data="exam_type:файнал"),
    )
    builder.adjust(1)
    await message.answer(
        "Дата начала сохранена. К какому экзамену ты готовишься?\n\n"
        "Выбери тип экзамена:",
        reply_markup=builder.as_markup()
    )
    
    

@router.callback_query(F.data.startswith("exam_type:"), AddCourse.waiting_for_exam_type)
async def add_course_exam_type_callback(callback: CallbackQuery, state: FSMContext):
    exam_type = callback.data.split(":")[1]
    await state.update_data(exam_type=exam_type)
    await state.set_state(AddCourse.waiting_for_date)
    rec = {
        "мидка": "Рекомендуется ~5 лекций, экзамен обычно на 5-й неделе.",
        "эндка": "Рекомендуется ~5 лекций, экзамен обычно на 10-й неделе.",
        "файнал": "Рекомендуется ~10 лекций (включая предыдущие), экзамен на 11-12 неделе."
    }
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Тип экзамена выбран: <b>{exam_type}</b>.\n{rec.get(exam_type, '')}\n\n"
        "Теперь введите дату экзамена в формате ДД.MM.ГГГГ (в 2025 году).",
        parse_mode="HTML"
    )
# ...existing code...

@router.callback_query(F.data.startswith("block_learned:"))
async def block_learned_callback(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    block_id = ObjectId(callback.data.split(":")[1])
    block = await db["blocks"].find_one({"_id": block_id})
    if not block:
        await callback.answer("Блок не найден.", show_alert=True)
        return
    await db["blocks"].update_one({"_id": block_id}, {"$set": {"learned_at": now_utc()}})
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ Блок отмечен как изученный!")

    # --- Проверяем, все ли блоки лекции изучены ---
    lecture_id = block.get("lecture_id")
    user_id = callback.from_user.id
    total_blocks = await db["blocks"].count_documents({"lecture_id": lecture_id, "user_id": user_id})
    learned_blocks = await db["blocks"].count_documents({"lecture_id": lecture_id, "user_id": user_id, "learned_at": {"$exists": True}})
    if total_blocks > 0 and total_blocks == learned_blocks:
        # Все блоки лекции изучены — предлагаем пройти квиз
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="📝 Пройти квиз по лекции", callback_data=f"start_quiz:{lecture_id}"))
        await callback.message.answer(
            "🎉 Ты изучил все блоки этой лекции!\nГотов проверить себя?",
            reply_markup=keyboard.as_markup()
        )

@router.message(Command("delete_course"))
async def delete_course(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=10)
    if not user_courses:
        await message.answer("У вас нет добавленных курсов.")
        return

    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(
            text=course['title'],
            callback_data=f"delete_course:{course['_id']}"
        ))
    builder.adjust(1)
    await message.answer("Выберите курс для удаления:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("show_files:"))
async def show_files_for_course(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    if await is_blocked(callback.from_user.id, db):
        await callback.message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    course_id = ObjectId(callback.data.split(":")[1])
    lectures = await db["lectures"].find({"course_id": course_id}).to_list(length=20)
    if not lectures:
        await callback.message.answer("Для этого курса нет загруженных файлов.")
        return

    builder = InlineKeyboardBuilder()
    for lec in lectures:
        builder.add(InlineKeyboardButton(
            text=lec['filename'],
            callback_data=f"send_file:{lec['_id']}"
        ))
    builder.adjust(1)
    await callback.message.answer("Выберите файл для скачивания:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("send_file:"))
async def send_file_to_user(callback: CallbackQuery, bot: Bot, db: AsyncIOMotorDatabase):
    if await is_blocked(callback.from_user.id, db):
        await callback.message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    file_id = ObjectId(callback.data.split(":")[1])
    lecture = await db["lectures"].find_one({"_id": file_id})
    if not lecture:
        await callback.message.answer("Файл не найден.")
        return

    # Получаем file_id Telegram (вы должны сохранять его при загрузке)
    tg_file_id = lecture.get("tg_file_id")
    if not tg_file_id:
        await callback.message.answer("Файл не может быть отправлен (нет file_id Telegram).")
        return

    await bot.send_document(chat_id=callback.from_user.id, document=tg_file_id, caption=lecture['filename'])

@router.callback_query(F.data.startswith("delete_course:"))
async def confirm_delete_course(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    if await is_blocked(callback.from_user.id, db):
        await callback.message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    course_id = ObjectId(callback.data.split(":")[1])
    course = await db["courses"].find_one({"_id": course_id})
    if not course:
        await callback.message.answer("Курс не найден.")
        return
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="Да, удалить", callback_data=f"confirm_delete_course:{course_id}"),
        InlineKeyboardButton(text="Нет", callback_data="cancel_delete")
    )
    await callback.message.answer(f"Вы уверены, что хотите удалить курс «{course['title']}»?", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("confirm_delete_course:"))
async def really_delete_course(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    course_id = ObjectId(callback.data.split(":")[1])
    course = await db["courses"].find_one({"_id": course_id})
    await db["courses"].delete_one({"_id": course_id})
    await db["lectures"].delete_many({"course_id": course_id})
    await db["blocks"].delete_many({"course_id": course_id})
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Курс «{course['title']}» и все связанные материалы удалены.")

@router.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Удаление отменено.")
# --- Хендлер для /start ---
@router.message(CommandStart())
async def start(message: Message, db: AsyncIOMotorDatabase):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    role = "admin" if user_id in ADMIN_IDS else "user"
    await db["users"].update_one(
        {"telegram_id": user_id},
        {"$set": {"name": user_name, "role": role}},
        upsert=True
    )
    await message.answer(
        f"Привет, {user_name}!\nЯ твой интеллектуальный помощник для учебы.\n\n"
        "<b>С чего начать:</b>\n"
        "1. Добавь курс с помощью /add_course.\n"
        "2. Загрузи файл с лекцией (.pdf или .docx).\n"
        "3. Выбери удобный период суток для получения учебных сообщений.\n\n"
        "ℹ️ Для подробной справки по всем командам напиши /help."
    )




# ...existing code...

from typing import List
from math import ceil

PERIOD_HOUR_PREFERRED = {
    "утро": 9,   # отправка в 09:00
    "день": 15,  # отправка в 15:00
    "вечер": 19  # отправка в 19:00
}

def ensure_aware(dt: datetime.datetime) -> datetime.datetime:
    if dt is None:
        return None
    # Если в БД хранят naive datetime — считаем, что это UTC и конвертируем в LOCAL_TZ
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(LOCAL_TZ)

def compute_send_schedule(
    start_dt: datetime.datetime,
    exam_dt: datetime.datetime,
    now: datetime.datetime,
    notification_period: str,
    blocks_left: int
) -> List[datetime.datetime]:
    """
    Возвращает список timezone-aware дат/времён (Asia/Almaty), когда бот должен отправлять блоки.
    Правила:
      - Если до экзамена > 1 день: распределяем по дням в выбранный период (preferred hour).
      - Если до экзамена == 1 день или меньше: распределяем по оставшимся часам (по одному слоту в час).
    Возвращается список длины blocks_left (возможно несколько блоков в один слот).
    """
    start_dt = ensure_aware(start_dt)
    exam_dt = ensure_aware(exam_dt)
    now = ensure_aware(now)

    if blocks_left <= 0:
        return []

    pref_hour = PERIOD_HOUR_PREFERRED.get(notification_period, 15)

    # Количество целых дней до экзамена (если экзамен сегодня -> 0)
    days_left = (exam_dt.date() - now.date()).days
    # если экзамен в будущем, days_left >= 0; используем days_for_distribution = max(1, days_left)
    if days_left >= 1:
        # Для распределения учитываем: если сегодня ещё можно отправить в preferred hour ( hour > now.hour )
        slots = []
        for day_offset in range(0, days_left):
            candidate_date = (now.date() + datetime.timedelta(days=day_offset))
            slot_dt = datetime.datetime(candidate_date.year, candidate_date.month, candidate_date.day,
                                        pref_hour, 0, 0, tzinfo=LOCAL_TZ)
            # если slot_dt уже прошёл относительно now, пропускаем
            if slot_dt <= now:
                continue
            slots.append(slot_dt)

        # Если слотов нет (например, preferred hour уже прошёл сегодня и days_left==1), добавим завтрашний слот
        if not slots:
            next_day = now.date() + datetime.timedelta(days=1)
            slots.append(datetime.datetime(next_day.year, next_day.month, next_day.day, pref_hour, 0, 0, tzinfo=LOCAL_TZ))

        days_for_distribution = len(slots)
        # blocks per day (ceil чтобы успеть)
        per_slot = ceil(blocks_left / days_for_distribution)
        schedule = []
        i = 0
        for slot in slots:
            for _ in range(per_slot):
                if i >= blocks_left:
                    break
                schedule.append(slot)
                i += 1
            if i >= blocks_left:
                break
        return schedule

    else:
        # Экзамен сегодня или через несколько часов -> распределяем по часам до экзамена
        total_hours_left = max(1, int((exam_dt - now).total_seconds() // 3600))
        schedule = []
        per_hour = ceil(blocks_left / total_hours_left)
        for h in range(total_hours_left):
            slot_dt = now + datetime.timedelta(hours=h+1)  # начинаем с next hour
            # округлим до начала часа
            slot_dt = slot_dt.replace(minute=0, second=0, microsecond=0)
            slot_dt = slot_dt.astimezone(LOCAL_TZ)
            for _ in range(per_hour):
                if len(schedule) >= blocks_left:
                    break
                schedule.append(slot_dt)
            if len(schedule) >= blocks_left:
                break
        return schedule

# --- Команда предпросмотра рассылки ---
@router.message(Command("preview_schedule"))
async def preview_schedule_start(message: Message, db: AsyncIOMotorDatabase):
    if await is_blocked(message.from_user.id, db):
        await message.answer("❌ Ваш аккаунт заблокирован администратором.")
        return
    user_courses = await db["courses"].find({"user_id": message.from_user.id}).to_list(length=20)
    if not user_courses:
        await message.answer("У вас нет добавленных курсов.")
        return
    builder = InlineKeyboardBuilder()
    for course in user_courses:
        builder.add(InlineKeyboardButton(text=course['title'], callback_data=f"preview_schedule:{course['_id']}"))
    builder.adjust(1)
    await message.answer("Выберите курс для предпросмотра расписания отправки блоков:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("preview_schedule:"))
async def preview_schedule_callback(callback: CallbackQuery, db: AsyncIOMotorDatabase):
    course_id = ObjectId(callback.data.split(":")[1])
    course = await db["courses"].find_one({"_id": course_id})
    if not course:
        await callback.message.answer("Курс не найден.")
        return

    # подсчитать количество неотправленных блоков
    blocks_left = await db["blocks"].count_documents({"course_id": course_id, "sent_at": {"$exists": False}})
    if blocks_left == 0:
        await callback.message.answer("Нет незапланированных/неотправленных блоков для этого курса.")
        return

    start_dt = course.get("start_date")
    exam_dt = course.get("exam_date")
    period = course.get("notification_period", "день")
    now = now_local()

    # если даты в БД хранятся как naive, сделаем aware
    start_dt = ensure_aware(start_dt) if start_dt else None
    exam_dt = ensure_aware(exam_dt) if exam_dt else None
    if not start_dt or not exam_dt:
        await callback.message.answer("У курса не заполнены даты (start_date/exam_date).")
        return

    schedule = compute_send_schedule(start_dt, exam_dt, now, period, blocks_left)
    human_lines = []
    for idx, dt_send in enumerate(schedule, start=1):
        human_lines.append(f"{idx}. {dt_send.strftime('%d.%m.%Y %H:%M %Z')}")

    # покажем первые 50 элементов, если много
    text = (
        f"Курс: {course.get('title')}\n"
        f"Незагруженных блоков: {blocks_left}\n"
        f"Период уведомлений: {period}\n\n"
        "Расписание отправки (пример):\n" + ("\n".join(human_lines[:50]))
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(text)

# ...existing code...


# ...existing code...
from typing import Optional

def compute_blocks_to_send_now(exam_dt: datetime.datetime, now: datetime.datetime, blocks_left: int) -> int:
    if blocks_left <= 0:
        return 0
    days_left = max(1, (exam_dt.date() - now.date()).days)
    if days_left > 1:
        return max(1, math.ceil(blocks_left / days_left))
    hours_left = max(1, int((exam_dt - now).total_seconds() // 3600))
    return max(1, math.ceil(blocks_left / hours_left))

async def send_next_blocks(course_id: ObjectId, bot: Bot, db: AsyncIOMotorDatabase, count: int, user_id: int):
    cursor = db["blocks"].find({"course_id": course_id, "user_id": user_id, "sent_at": {"$exists": False}}).sort("block_index", 1).limit(count)
    blocks = [b async for b in cursor]
    sent = 0
    for block in blocks:
        course = await db["courses"].find_one({"_id": course_id})
        title = course.get("title", "Курс")
        summary = block.get("summary", "Нет краткого содержания.")
        explanation = block.get("explanation", "Нет объяснения.")
        text_to_send = (
            f"🔔 <b>Новый материал для изучения!</b>\n\n"
            f"📚 <b>Курс:</b> «{title}»\n\n"
            f"<b>Краткое содержание:</b>\n{summary}\n\n"
            f"<b>Простыми словами:</b>\n{explanation}"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Я изучил", callback_data=f"block_learned:{block['_id']}")]
            ]
        )
        try:
            await bot.send_message(chat_id=user_id, text=text_to_send, parse_mode="HTML", reply_markup=keyboard)
            await db["blocks"].update_one({"_id": block["_id"]}, {"$set": {"sent_at": now_utc()}})
            sent += 1
        except Exception as e:
            logging.error(f"Не удалось отправить блок {block['_id']} пользователю {user_id}: {e}")
    return sent

@router.callback_query(F.data.startswith("simulate_send:"))
async def simulate_send_callback(callback: CallbackQuery, bot: Bot, db: AsyncIOMotorDatabase):
    course_id = ObjectId(callback.data.split(":")[1])
    course = await db["courses"].find_one({"_id": course_id})
    if not course:
        await callback.message.answer("Курс не найден.")
        return
    exam_dt = ensure_aware(course.get("exam_date"))
    now = now_local()
    blocks_left = await db["blocks"].count_documents({"course_id": course_id, "user_id": callback.from_user.id, "sent_at": {"$exists": False}})
    if blocks_left == 0:
        await callback.message.answer("Нет неотправленных блоков для этого курса.")
        return
    to_send = compute_blocks_to_send_now(exam_dt, now, blocks_left)
    sent = await send_next_blocks(course_id, bot, db, to_send, callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Симуляция: отправлено {sent} блока(ов) для курса «{course.get('title')}».")
# ...existing code...



@router.callback_query(F.data.startswith("start_quiz:"))
async def start_lecture_quiz(callback: CallbackQuery, state: FSMContext, db: AsyncIOMotorDatabase):
    lecture_id = ObjectId(callback.data.split(":")[1])
    # Собираем все вопросы из blocks этой лекции
    blocks = await db["blocks"].find({"lecture_id": lecture_id}).sort("block_index", 1).to_list(length=100)
    questions = []
    for block in blocks:
        for q in block.get("questions", []):
            questions.append({
                "block_id": str(block["_id"]),
                "question": q.get("question"),
                "options": q.get("options"),
                "answer": q.get("answer"),
                "explanation": q.get("explanation", "")
            })
    if not questions:
        await callback.message.answer("Нет вопросов для квиза по этой лекции.")
        return
    await state.update_data(
        quiz_questions=questions,
        quiz_current=0,
        quiz_correct=0,
        quiz_answers=[]
    )
    await state.set_state(LectureQuiz.answering)
    await send_next_quiz_question(callback.message, state, db)
    
async def send_next_quiz_question(message: Message, state: FSMContext, db: AsyncIOMotorDatabase):
    data = await state.get_data()
    questions = data["quiz_questions"]
    idx = data["quiz_current"]
    if idx >= len(questions):
        await show_quiz_result(message, state, db)
        await state.clear()
        return
    q = questions[idx]
    builder = InlineKeyboardBuilder()
    for i, opt in enumerate(q["options"]):
        builder.add(InlineKeyboardButton(
            text=f"{chr(65+i)}. {opt}",
            callback_data=f"quiz_answer:{idx}:{i}"
        ))
    builder.adjust(1)
    await message.answer(
        f"<b>Вопрос {idx+1} из {len(questions)}:</b>\n{q['question']}",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
 
@router.message(F.text, StateFilter(None))
async def handle_text_question(message: Message, db: AsyncIOMotorDatabase):
    if message.chat.id == ADMIN_GROUP_ID:
        return
    if message.text.startswith("/"):
        return  # Не обрабатываем команды как обычный текст
    question = message.text.strip()
    answer = await quiz_service.get_generic_answer(question)
    await db["qa_history"].insert_one({
        "user_id": message.from_user.id,
        "question": question,
        "answer": answer,
        "created_at": datetime.datetime.utcnow()
    })
    max_len = 4096
    for i in range(0, len(answer), max_len):
        await message.answer(answer[i:i+max_len])
        