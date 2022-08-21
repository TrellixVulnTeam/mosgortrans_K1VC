import asyncio
import sys
from http import HTTPStatus
from pathlib import Path

from aiogram import Dispatcher
from aiogram.types import Update
from aiogram.utils.executor import start_polling
from aiohttp import web

sys.path.append(str(Path(__file__).parent.parent))

from app.core.bot import bot, dispatcher
from app.core.logger import logger
from app.core.scheduler import asyncio_schedule
from app.settings import (
    API_TOKEN,
    START_WITH_WEBHOOK,
    WEBAPP_HOST,
    WEBAPP_PORT,
    WEBHOOK_PATH,
    WEBHOOK_URL,
)

queue = asyncio.Queue()  # type: ignore


async def bot_startup() -> None:
    await bot.set_webhook(WEBHOOK_URL)
    loop = asyncio.get_running_loop()
    loop.create_task(get_updates_from_queue())
    logger.info(f'Webhook set to {WEBHOOK_URL}'.replace(API_TOKEN, '{BOT_API_TOKEN}'))
    asyncio_schedule()


async def bot_shutdown() -> None:
    logger.warning('Shutting down..')

    # Remove webhook (not acceptable in some cases)
    await bot.delete_webhook()

    # Close DB connection (if used)
    await dispatcher.storage.close()
    await dispatcher.storage.wait_closed()

    session = await bot.get_session()
    if session and not session.closed:
        await session.close()
        await asyncio.sleep(0.2)

    logger.warning('Bye!')


async def aiogram_startup(dp: Dispatcher) -> None:
    await bot_startup()


async def aiogram_shutdown(dp: Dispatcher) -> None:
    await bot_shutdown()


async def on_startup_gunicorn(app: web.Application) -> None:
    logger.info("Start bot with webhook")
    await bot_startup()


async def on_shutdown_gunicorn(app: web.Application) -> None:
    await bot_shutdown()


def bot_polling() -> None:
    logger.info("Start bot in polling mode")
    start_polling(
        dispatcher=dispatcher,
        skip_updates=True,
        on_startup=aiogram_startup,
        on_shutdown=aiogram_shutdown,
    )


async def put_updates_on_queue(request: web.Request) -> web.Response:
    """
    Listen {WEBHOOK_PATH} and proxy post request to bot

    :param request:
    :return:
    """
    data = await request.json()
    tg_update = Update(**data)
    queue.put_nowait(tg_update)

    return web.Response(status=HTTPStatus.ACCEPTED)


async def get_updates_from_queue() -> None:

    while True:
        update = await queue.get()
        await dispatcher.process_update(update)
        await asyncio.sleep(0.1)


async def create_app() -> web.Application:
    application = web.Application()
    application.router.add_post(f'{WEBHOOK_PATH}/{API_TOKEN}', put_updates_on_queue)
    application.on_startup.append(on_startup_gunicorn)
    application.on_shutdown.append(on_shutdown_gunicorn)
    return application


if __name__ == '__main__':

    if START_WITH_WEBHOOK:
        app = create_app()
        web.run_app(app=app, host=WEBAPP_HOST, port=WEBAPP_PORT)
    else:
        bot_polling()
