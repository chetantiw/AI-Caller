
"""

outbound_call.py - Trigger outbound calls via Exotel API

Usage:

  Single call:  python outbound_call.py +91XXXXXXXXXX "Name" "Company"

  Campaign:     python outbound_call.py --campaign leads/sample_leads.csv 30

"""

import os, sys, time, csv, subprocess

from dotenv import load_dotenv

from loguru import logger



load_dotenv()



ACCOUNT_SID = os.getenv("EXOTEL_ACCOUNT_SID", "mutechautomation1")

API_KEY     = os.getenv("EXOTEL_API_KEY")

API_TOKEN   = os.getenv("EXOTEL_API_TOKEN")

FROM_NUMBER = "07314854688"

CALLER_ID   = "+917314854688"

APP_URL     = f"http://my.exotel.com/{ACCOUNT_SID}/exoml/start/{ACCOUNT_SID}+Landing+Flow"

API_URL     = f"https://api.exotel.com/v1/Accounts/{ACCOUNT_SID}/Calls/connect"



def make_call(to_number: str, name: str = "", company: str = "") -> dict:

    # Normalize number

    to_number = to_number.strip().replace(" ", "").replace("-", "")

    if to_number.startswith("0"):

        to_number = "+91" + to_number[1:]

    elif len(to_number) == 10:

        to_number = "+91" + to_number

    elif not to_number.startswith("+"):

        to_number = "+91" + to_number



    logger.info(f"Calling {to_number} ({name} | {company})")



    try:

        result = subprocess.run([

            "curl", "-s", "-X", "POST", API_URL,

            "-u", f"{API_KEY}:{API_TOKEN}",

            "-d", f"From={FROM_NUMBER}",

            "-d", f"To={to_number}",

            "-d", f"CallerId={CALLER_ID}",

            "-d", f"Url={APP_URL}",

            "-d", "TimeLimit=300",

            "-d", "TimeOut=30",

        ], capture_output=True, text=True, timeout=15)



        response = result.stdout

        if "<Status>in-progress</Status>" in response or "<Status>queued</Status>" in response:

            logger.info(f"✅ Call initiated to {to_number} ({name})")

            return {"success": True, "response": response}

        else:

            logger.error(f"❌ Failed for {to_number}: {response[:200]}")

            return {"success": False, "error": response}



    except Exception as e:

        logger.error(f"❌ Exception: {e}")

        return {"success": False, "error": str(e)}



def run_campaign(csv_file: str, delay_seconds: int = 30):

    """Call all leads from CSV with delay between calls"""

    logger.info(f"Starting campaign from: {csv_file}")



    with open(csv_file, newline="", encoding="utf-8-sig") as f:  # utf-8-sig handles BOM

        reader = csv.DictReader(f)

        leads = list(reader)

    

    # Debug: show columns found

    if leads:

        logger.info(f"CSV columns: {list(leads[0].keys())}")



    logger.info(f"Total leads: {len(leads)}")

    success = 0

    failed  = 0



    for i, lead in enumerate(leads):

        number  = lead.get("phone", lead.get("mobile", lead.get("number", "")))

        name    = lead.get("name", lead.get("contact_name", ""))

        company = lead.get("company", lead.get("company_name", ""))



        if not number:

            logger.warning(f"Row {i+1}: no phone number, skipping")

            continue



        result = make_call(number, name, company)

        if result["success"]:

            success += 1

        else:

            failed += 1



        if i < len(leads) - 1:

            logger.info(f"Waiting {delay_seconds}s before next call...")

            time.sleep(delay_seconds)



    logger.info(f"Campaign complete! ✅ {success} success | ❌ {failed} failed")



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

        print("  Single:   python outbound_call.py +91XXXXXXXXXX 'Name' 'Company'")

        print("  Campaign: python outbound_call.py --campaign leads/sample_leads.csv 30")

