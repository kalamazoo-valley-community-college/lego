from fastapi import FastAPI, Request
from fastapi.responses import Response, HTMLResponse
from datetime import datetime, timedelta
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager

scheduler = AsyncIOScheduler()

DEFAULT_DURATION_DAYS = 15
## derived global constants ##
DEFAULT_DURATION_SECS = DEFAULT_DURATION_DAYS * 86400
DEFAULT_RENEWAL_DAYS = 2*DEFAULT_DURATION_DAYS // 3
DEFAULT_RENEWAL_SECS = DEFAULT_DURATION_DAYS * 86400

# Create db on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect("app.db") as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT,
            duration INTEGER
        )''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT,
            datetime DATETIME,
            FOREIGN KEY (hostname) REFERENCES hosts (hostname)
        )''')

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
        most_recent_dt = datetime.strptime(host["most_recent_record"], "%Y-%m-%d %H:%M:%S")
        age_in_seconds = (now - most_recent_dt).total_seconds()
        if age_in_seconds >= DEFAULT_DURATION_SECS:
            expired.append(host)
        elif age_in_seconds >= DEFAULT_RENEWAL_SECS:
            expiring.append(host)
    
    await send_report_email(expired, expiring)

async def send_report_email(expired, expiring):
    print(expired)
    print(expiring)

@app.post("/records")
async def create_record(request: Request):
    # Get the current time and insert the record
    body = await request.json()
    if "domain" not in body:
        raise Exception("Received a record that did not contain a hostname.")
    hostname = body["domain"]
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    async with aiosqlite.connect("app.db") as db:
        # sqlite does not handle insertion cascades in the normal way, so insert or ignore
        await db.execute(
            "INSERT OR IGNORE INTO hosts (hostname, duration) VALUES (?, ?)",
            (hostname, 15)  # Default duration days for new hosts
            # SCHEDULE YOUR ACME JOB FOR 2/3 THIS VALUE
        )
        await db.execute(
            "INSERT INTO records (hostname, datetime) VALUES (?, ?)",
            (hostname, current_time)
        )
        await db.commit()



@app.get("/", response_class=HTMLResponse)
async def read_spreadsheet():
    return """
    <html>
    <head>
        <style>
            .red { color: red; }
            .orange { color: orange; }
            .green { color: green; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid black; padding: 8px; text-align: left; }
        </style>
    </head>
    <body>
        <h1>Host Records</h1>
        <table id="spreadsheet">
            <thead>
                <tr>
                    <th>Hostname</th>
                    <th>Most Recent Record</th>
                    <th>Next Expected Renewal</th>
                </tr>
            </thead>
            <tbody>
                <!-- Data will be inserted here -->
            </tbody>
        </table>

        <script>
            async function fetchData() {
                const response = await fetch('/hosts');
                const data = await response.json();
                const tableBody = document.querySelector("#spreadsheet tbody");

                data.forEach(row => {
                    const tr = document.createElement("tr");
                    const hostnameCell = document.createElement("td");
                    hostnameCell.textContent = row.hostname;

                    const recordCell = document.createElement("td");
                    recordCell.textContent = row.most_recent_record;

                    const renewalCell = document.createElement("td");
                    renewalCell.textContent = row.next_expected_renewal;

                    const lastRecordDate = new Date(row.most_recent_record);
                    const expectedRenewalDate = new Date(row.next_expected_renewal);
                    const now = new Date();

                    if (now > expectedRenewalDate) {
                        if ((now - lastRecordDate) > row.duration * 24 * 60 * 60 * 1000) {
                            recordCell.classList.add("red");  // Expired
                        } else {
                            recordCell.classList.add("orange");  // Didn't renew but not expired
                        }
                    } else {
                        recordCell.classList.add("green");  // Healthy
                    }

                    tr.appendChild(hostnameCell);
                    tr.appendChild(recordCell);
                    tr.appendChild(renewalCell);
                    tableBody.appendChild(tr);
                });
            }
            fetchData();
        </script>
    </body>
</html>
"""

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
            next_expected_renewal = most_recent_dt + timedelta(days=(2/3) * duration_days)
            next_expected_renewal_str = next_expected_renewal.strftime("%Y-%m-%d %H:%M:%S")
        else:
            next_expected_renewal_str = "N/A"

        hosts.append({
            "hostname": hostname,
            "duration": duration_days,
            "most_recent_record": most_recent,
            "next_expected_renewal": next_expected_renewal_str
        })

    return hosts

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4444)