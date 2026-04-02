
"""

app/exotel_handler.py

Triggers outbound calls via Exotel API

"""

import os

import aiohttp

from loguru import logger

from dotenv import load_dotenv



load_dotenv()



async def make_outbound_call(to_number: str, lead_id: str = None) -> str:

    api_key    = os.getenv("EXOTEL_API_KEY")

    api_token  = os.getenv("EXOTEL_API_TOKEN")

    account_sid = os.getenv("EXOTEL_ACCOUNT_SID")

    from_number = os.getenv("EXOTEL_VIRTUAL_NUMBER")

    subdomain  = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

    public_url = os.getenv("PUBLIC_URL", "https://ai.mutechautomation.com")



    # Exotel Connect API URL

    url = f"https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{account_sid}/Calls/connect"



    # StatusCallback to track call events

    status_callback = f"{public_url}/exotel/status"

    if lead_id:

        status_callback += f"?lead_id={lead_id}"



    payload = {

        "From": to_number,           # Customer number (called first)

        "To": from_number,           # ExoPhone (then connected to Voicebot)

        "CallerId": from_number,     # Caller ID shown to customer

        "StatusCallback": status_callback,

        "StatusCallbackEvents[0]": "terminal",

    }



    logger.info(f"Exotel outbound | to: {to_number} | from: {from_number}")



    async with aiohttp.ClientSession() as session:

        async with session.post(url, data=payload) as resp:

            text = await resp.text()

            logger.info(f"Exotel response [{resp.status}]: {text[:200]}")

            if resp.status not in (200, 201):

                raise Exception(f"Exotel call failed [{resp.status}]: {text}")

            return text

