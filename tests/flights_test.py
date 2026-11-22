import asyncio
import json
import sys

sys.path.insert(0, r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2")
import aiohttp
from dotenv import load_dotenv
load_dotenv(r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2\.env")
import flights_provider as f

async def main():
    print("config:", f._config()[0][:6] + "...", f._config()[1], f._config()[2])
    print("status:", f.status())
    async with aiohttp.ClientSession() as http:
        result = await f.search(http, "Rome", "2026-11-23")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:3000])

asyncio.run(main())
