import asyncio
import logging
import os
import json
from dotenv import load_dotenv

logging.basicConfig(level=logging.DEBUG)

load_dotenv()

async def main():
    email = os.getenv("HON_EMAIL")
    password = os.getenv("HON_PASSWORD")

    if not email or not password:
        print("ERROR: HON_EMAIL or HON_PASSWORD not set in .env")
        return

    print(f"Connecting as {email} ...")

    try:
        from pyhon.connection.api import HonAPI

        async with HonAPI(email=email, password=password) as api:
            # Print auth token (first 40 chars only)
            try:
                token = api._hon.auth.access_token
                print(f"Access token: {token[:40]}..." if token else "Access token: EMPTY")
            except Exception as e:
                print(f"Could not read token: {e}")

            # Call the endpoint directly and print the raw response
            async with api._hon.get("https://api-iot.he.services/commands/v1/appliance") as resp:
                print(f"HTTP status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")

    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
