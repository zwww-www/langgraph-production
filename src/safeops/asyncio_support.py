import asyncio
import sys


def configure_loop() -> None:
    # psycopg async sockets require SelectorEventLoop on Windows.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
