import smtplib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from html import HTML
from pathlib import Path
from sys import exit
from urllib.parse import urlparse

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

scheduler = AsyncIOScheduler()

CLIENTS_BASE_DIR = Path("/etc/step-ca/reporting-plane/clients")

EMAIL_TO_ADDRESS = (
    "certificates@kvcc.edu"  # This should be the address of the ACME distribution group
)
EMAIL_FROM_ADDRESS = "acme_notifier@kvcc.edu"
EMAIL_SUBJECT = "¡TLS RENEWAL FAILURES!"
DEFAULT_DURATION_DAYS = 15
## derived global constants ##
DEFAULT_DURATION_SECS = DEFAULT_DURATION_DAYS * 86400
DEFAULT_RENEWAL_DAYS = 2 * DEFAULT_DURATION_DAYS // 3
DEFAULT_RENEWAL_SECS = DEFAULT_DURATION_DAYS * 86400


# Create db on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect("app.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            common_name TEXT UNIQUE
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host INTEGER,
            datetime DATETIME,
            provider TEXT,
            duration INTEGER,
            FOREIGN KEY (host) REFERENCES hosts (id)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS sans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record INTEGER,
            san TEXT,
            FOREIGN KEY (record) REFERENCES records (id),
            UNIQUE(record, san)
        )""")

        await db.commit()
        scheduler.start()
        scheduler.add_job(report_problem_hosts, "interval", hours=12)
        yield
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


async def report_problem_hosts():
    hosts = await get_records()
    expired, expiring = [], []
    now = datetime.now()

    for host in hosts:
        most_recent_dt = datetime.strptime(
            host["most_recent_record"], "%Y-%m-%d %H:%M:%S"
        )
        age_in_seconds = (now - most_recent_dt).total_seconds()
        if age_in_seconds >= DEFAULT_DURATION_SECS:
            expired.append(host)
        elif age_in_seconds >= DEFAULT_RENEWAL_SECS:
            expiring.append(host)

    await send_report_email(expired, expiring)


async def send_report_email(expired, expiring):
    report = ""
    if len(expired) > 0:
        report += """The following hosts have FAILED TO RENEW and ARE EXPIRED:\n"""
        report += "\n".join([x["common_name"] for x in expired])
        report += "\n"
    if len(expiring) > 0:
        report += """The following hosts have FAILED TO RENEW and WILL EXPIRE SOON:\n"""
        report += "\n".join([x["common_name"] for x in expired])
        report += "\n"
    if report == "":
        return
    msg = MIMEText(report)
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = EMAIL_FROM_ADDRESS
    msg["To"] = EMAIL_TO_ADDRESS
    s = smtplib.SMTP("smtp.kvcc.edu")
    s.sendmail(EMAIL_FROM_ADDRESS, [EMAIL_TO_ADDRESS], msg.as_string())
    s.quit()


@app.get("/client/{os_name}")
async def get_client(os_name: str):
    # Map OS names to binary file names
    os_name = os_name.lower()
    payloads = {
        "windows": "lego.exe",
        "macos": "lego_macos",
        "linux": "lego_linux",
    }

    if os_name not in payloads:
        return HTTPException(
            status_code=404,
            detail='Supported values are "linux", "windows", and "macos".',
        )

    file_path = CLIENTS_BASE_DIR / payloads[os_name]

    if not file_path.exists():
        raise HTTPException(
            status_code=404, detail="Valid input, but binary is missing. Contact admin."
        )
    if os_name == "windows":
        file_name = "lego.exe"
    else:
        file_name = "lego"
    return FileResponse(file_path, filename=file_name)


@app.post("/records")
async def create_record(request: Request):
    body = await request.json()
    if "cn" not in body or "certUrl" not in body or "sans" not in body:
        print(body)
        raise ValueError("Invalid record: missing cn, certUrl, or SANs.")

    common_name = body["cn"]
    sans = body["sans"]
    parsed_url = urlparse(body["certUrl"])
    provider = parsed_url.hostname
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    duration = body["duration"]

    async with aiosqlite.connect("app.db") as db:
        await db.execute(
            """
            INSERT INTO hosts (common_name) 
            VALUES (?) 
            ON CONFLICT(common_name) DO NOTHING
            """,
            (common_name,),
        )

        async with db.execute(
            "SELECT id FROM hosts WHERE common_name = ?", (common_name,)
        ) as cursor:
            host_row = await cursor.fetchone()
            if not host_row:
                raise ValueError("Failed to insert or retrieve host ID.")
            host_id = host_row[0]

        await db.execute(
            "INSERT INTO records (host, duration, datetime, provider) VALUES (?, ?, ?, ?)",
            (host_id, duration, current_time, provider),
        )

        async with db.execute("SELECT last_insert_rowid()") as cursor:
            record_row = await cursor.fetchone()
            if not record_row:
                raise ValueError("Failed to retrieve record ID.")
            record_id = record_row[0]

        for san in sans:
            await db.execute(
                """
                INSERT INTO sans (record, san) VALUES (?, ?)
                ON CONFLICT(record, san) DO NOTHING
                """,
                (record_id, san),
            )
        await db.commit()


@app.get("/", response_class=HTMLResponse)
async def read_spreadsheet():
    return HTML


@app.get("/records")
async def get_records():
    async with aiosqlite.connect("app.db") as db:
        query = """
        SELECT h.id, h.common_name,  r.duration, r.datetime, r.provider, 
        GROUP_CONCAT(s.san) AS sans
        FROM hosts h
        LEFT JOIN records r ON h.id = r.host
        LEFT JOIN sans s ON r.id = s.record
        WHERE r.datetime = (SELECT MAX(datetime) FROM records WHERE host = h.id)
        GROUP BY h.id, r.id
        """
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()

    hosts = []
    for row in rows:
        host_id, common_name, duration_days, most_recent, provider, sans = row

        if most_recent:
            most_recent_dt = datetime.strptime(most_recent, "%Y-%m-%d %H:%M:%S")
            next_expected_renewal = most_recent_dt + timedelta(
                days=(2 / 3) * duration_days
            )
            next_expected_renewal_str = next_expected_renewal.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            next_expected_renewal_str = datetime.fromtimestamp(0).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        if provider.endswith("kvcc.edu"):
            provider = "KVCC"
        elif provider.endswith("letsencrypt.org"):
            provider = "LetsEncrypt"
        else:
            provider = f"Unknown - {provider}"

        hosts.append(
            {
                "id": host_id,
                "common_name": common_name,
                "duration": duration_days,
                "most_recent_record": most_recent,
                "next_expected_renewal": next_expected_renewal_str,
                "provider": provider,
                "sans": sans.split(",") if sans else [],
            }
        )
    return hosts


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--certfile")
    parser.add_argument("--keyfile")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.debug:
        if args.port is not None:
            print("--port is no-op in debug mode. debug is http only on 4444")
        uvicorn.run(app, host="0.0.0.0", port=4444)
    else:
        if args.certfile is None or args.keyfile is None:
            print("--certfile and --keyfile are required in production mode")
            exit()
        if args.port is None:
            args.port = 443
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=int(args.port),
            ssl_keyfile=args.keyfile,
            ssl_certfile=args.certfile,
        )
