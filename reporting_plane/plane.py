import smtplib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

scheduler = AsyncIOScheduler()

EMAIL_TO_ADDRESS = ""  # This should be the address of the ACME distribution group
EMAIL_FROM_ADDRESS = "acme_notifier@kvcc.edu"
EMAIL_SUBJECT = "¡TLS RENEWAL FAILURES!"
DEFAULT_DURATION_DAYS = 15
## derived global constants ##
DEFAULT_DURATION_SECS = DEFAULT_DURATION_DAYS * 86400
DEFAULT_RENEWAL_DAYS = 2 * DEFAULT_DURATION_DAYS // 3
DEFAULT_RENEWAL_SECS = DEFAULT_DURATION_DAYS * 86400

HTML = Path("plane.html").read_text()

# Create db on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect("app.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT,
            duration INTEGER
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT,
            datetime DATETIME,
            FOREIGN KEY (hostname) REFERENCES hosts (hostname)
        )""")

        await db.commit()
        scheduler.start()
        scheduler.add_job(report_problem_hosts, "interval", hours=12)
        yield
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


async def report_problem_hosts():
    hosts = await get_hosts()
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
        report += "\n".join([x["hostname"] for x in expired])
        report += "\n"
    if len(expiring) > 0:
        report += """The following hosts have FAILED TO RENEW and WILL EXPIRE SOON:\n"""
        report += "\n".join([x["hostname"] for x in expired])
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


@app.post("/records")
async def create_record(request: Request):
    # Get the current time and insert the record
    body = await request.json()
    if "domain" not in body:
        raise Exception("Received a record that did not contain a hostname.")
    hostname = body["domain"]
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect("app.db") as db:
        # sqlite does not handle insertion cascades in the normal way, so insert or ignore
        await db.execute(
            "INSERT OR IGNORE INTO hosts (hostname, duration) VALUES (?, ?)",
            (hostname, 15),  # Default duration days for new hosts
            # SCHEDULE YOUR ACME JOB FOR 2/3 THIS VALUE
        )
        await db.execute(
            "INSERT INTO records (hostname, datetime) VALUES (?, ?)",
            (hostname, current_time),
        )
        await db.commit()


@app.get("/", response_class=HTMLResponse)
async def read_spreadsheet():
    return HTML

@app.get("/hosts")
async def get_hosts():
    async with aiosqlite.connect("app.db") as db:
        query = """
        SELECT DISTINCT h.hostname, h.duration, r.datetime
        FROM hosts h
        LEFT JOIN records r ON h.hostname = r.hostname
        WHERE r.datetime = (SELECT MAX(datetime) FROM records WHERE hostname = h.hostname)
        """
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()

    hosts = []
    for row in rows:
        hostname, duration_days, most_recent = row[0], row[1], row[2]

        if most_recent:
            most_recent_dt = datetime.strptime(most_recent, "%Y-%m-%d %H:%M:%S")
            next_expected_renewal = most_recent_dt + timedelta(
                days=(2 / 3) * duration_days
            )
            next_expected_renewal_str = next_expected_renewal.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            next_expected_renewal_str = "N/A"

        hosts.append(
            {
                "hostname": hostname,
                "duration": duration_days,
                "most_recent_record": most_recent,
                "next_expected_renewal": next_expected_renewal_str,
            }
        )

    return hosts


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=4444)
