from fastapi import FastAPI, Request
from fastapi.responses import Response, HTMLResponse
from datetime import datetime, timedelta
import aiosqlite

app = FastAPI()

"""
class Host:
    hostname: str
    duration: int  # duration in seconds

class Record:
    host_id: int
    datetime: datetime
"""

# Create db on startup
@app.on_event("startup")
async def startup():
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
                    const lastRecordDate = new Date(row.most_recent_record);
                    const durationInDays = row.duration; // Assuming `duration` is in days
                    const now = new Date();

                    // Convert duration from days to milliseconds
                    const durationInMilliseconds = durationInDays * 24 * 60 * 60 * 1000;

                    // Compare the difference in time
                    if ((now - lastRecordDate) > durationInMilliseconds) {
                        recordCell.classList.add("red");
                    } else {
                        recordCell.classList.add("green");
                    }

                    tr.appendChild(hostnameCell);
                    tr.appendChild(recordCell);
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
        SELECT DISTINCT (h.hostname), h.duration, r.datetime
        FROM hosts h
        LEFT JOIN records r ON h.hostname = r.hostname
        WHERE r.datetime = (SELECT MAX(datetime) FROM records WHERE hostname = h.hostname)
        """
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()

    hosts = []
    for row in rows:
        print(row)
        hostname, duration_days, most_recent = row[0], row[1], row[2]
        
        # Prepare the data in the format needed for the frontend
        hosts.append({
            "hostname": hostname,
            "duration": duration_days,
            "most_recent_record": most_recent
        })

    return hosts

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4444)