import asyncio
import datetime
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from handlers import common, schedule

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

async def motivation_scheduler(bot, db):
    while True:
        now = datetime.datetime.now(common.LOCAL_TZ)
        if now.hour == 9 and now.minute == 0:
            await common.send_motivation_to_all(bot, db)
            await asyncio.sleep(60)
        await asyncio.sleep(30)

async def main():
    mongo_client = AsyncIOMotorClient(config.MONGO_URL)
    db = mongo_client[config.MONGO_DB_NAME]
    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")

    default_properties = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=config.BOT_TOKEN, default=default_properties)
    dp = Dispatcher()

    dp.workflow_data['db'] = db
    dp.workflow_data['scheduler'] = scheduler
    dp.workflow_data['bot'] = bot

    dp.include_router(common.router)
    # dp.include_router(quiz.router)
    # dp.include_router(schedule.router)

    scheduler.add_job(
        schedule.send_daily_blocks_and_reminder,
        'cron',
        minute=0,
        args=(bot, db)
    )
    scheduler.add_job(
        common.send_motivation_to_all,
        'cron',
        hour=9,
        minute=0,
        args=(bot, db)
    )
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске бота: {e}")