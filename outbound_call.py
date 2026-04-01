
"""

outbound_call.py

Trigger outbound calls via Exotel API

Usage: 

  Single call:   python outbound_call.py +91XXXXXXXXXX "Name" "Company"

  From CSV:      python outbound_call.py --campaign leads/sample_leads.csv

"""

import os, sys, time, csv, requests

from dotenv import load_dotenv

from loguru import logger



load_dotenv()



ACCOUNT_SID = os.getenv("EXOTEL_ACCOUNT_SID", "mutechautomation1")

API_KEY     = os.getenv("EXOTEL_API_KEY")

API_TOKEN   = os.getenv("EXOTEL_API_TOKEN")

FROM_NUMBER = "07314854688"  # Landline - approved for outbound

CALLER_ID   = "+917314854688"

APP_URL     = f"http://my.exotel.com/{ACCOUNT_SID}/exoml/start/{ACCOUNT_SID}+Landing+Flow"

API_URL     = f"https://api.exotel.com/v1/Accounts/{ACCOUNT_SID}/Calls/connect"



def make_call(to_number: str, name: str = "", company: str = "") -> dict:

    # Normalize number

    to_number = to_number.strip().replace(" ", "").replace("-", "")

    if to_number.startswith("0"):

        to_number = "+91" + to_number[1:]

    elif not to_number.startswith("+"):

        to_number = "+91" + to_number



    logger.info(f"Calling {to_number} ({name} | {company})")



    try:

        r = requests.post(

            API_URL,

            auth=(API_KEY, API_TOKEN),

            data={

                "From": FROM_NUMBER,

                "To": to_number,

                "CallerId": CALLER_ID,

                "Url": APP_URL,

                "TimeLimit": 300,

                "TimeOut": 30,

                "CustomField": f"{name}|{company}",

            },

            timeout=10

        )

        if r.status_code == 200:

            logger.info(f"✅ Call initiated to {to_number}")

            return {"success": True}

        else:

            logger.error(f"❌ Failed {r.status_code}: {r.text[:200]}")

            return {"success": False, "error": r.text}

    except Exception as e:

        logger.error(f"❌ Exception: {e}")

        return {"success": False, "error": str(e)}



def run_campaign(csv_file: str, delay_seconds: int = 30):

    """Call all leads from CSV file with delay between calls"""

    logger.info(f"Starting campaign from {csv_file}")

    with open(csv_file, newline="") as f:

        reader = csv.DictReader(f)

        leads = list(reader)



    logger.info(f"Total leads: {len(leads)}")

    success = 0

    failed = 0



    for i, lead in enumerate(leads):

        number  = lead.get("phone", lead.get("mobile", lead.get("number", "")))

        name    = lead.get("name", lead.get("contact_name", ""))

        company = lead.get("company", lead.get("company_name", ""))



        if not number:

            logger.warning(f"Skipping row {i+1} - no phone number")

            continue



        result = make_call(number, name, company)

        if result["success"]:

            success += 1

        else:

            failed += 1



        if i < len(leads) - 1:

            logger.info(f"Waiting {delay_seconds}s before next call...")

            time.sleep(delay_seconds)



    logger.info(f"Campaign done! ✅ {success} success | ❌ {failed} failed")



if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "--campaign":

        csv_file = sys.argv[2] if len(sys.argv) > 2 else "leads/sample_leads.csv"

        delay    = int(sys.argv[3]) if len(sys.argv) > 3 else 30

        run_campaign(csv_file, delay)

    elif len(sys.argv) > 1:

        number  = sys.argv[1]

        name    = sys.argv[2] if len(sys.argv) > 2 else ""

        company = sys.argv[3] if len(sys.argv) > 3 else ""

        make_call(number, name, company)

    else:

        print("Usage:")

        print("  Single: python outbound_call.py +91XXXXXXXXXX 'Name' 'Company'")

        print("  Campaign: python outbound_call.py --campaign leads/sample_leads.csv 30")

