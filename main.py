import bot
import asyncio
import traceback
import logger
from datetime import datetime


async def run():
    await bot.main()

lgr = None

if __name__ == "__main__":
    try:
        lgr = logger.Logger()
        asyncio.run(run())
    except Exception as e:
        tb = traceback.format_exc()
        with open("log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}:\n{tb}\n")
        print(tb)
        if lgr is not None: lgr.err(e, "Error while running bot")
    input("Press Enter to exit...")
