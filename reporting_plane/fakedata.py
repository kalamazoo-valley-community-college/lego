import asyncio
import random
import string
from datetime import datetime, timedelta
import aiosqlite

# Function to generate random hostnames
def random_hostname():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

# Function to generate random duration (in days)
def random_duration():
    return random.randint(1, 90)  # Duration between 1 and 90 days

# Function to generate a random datetime within a range from now to several weeks in the future
def random_datetime():
    now = datetime.now()
    max_minutes = 60 * 24 * 7 * 3
    random_minutes = random.randint(-max_minutes, max_minutes)  # Random within several weeks
    random_time = now + timedelta(minutes=random_minutes)
    return random_time.strftime("%Y-%m-%d %H:%M:%S")

# Function to insert random data into the database
async def insert_random_data():
    async with aiosqlite.connect("app.db") as db:
        num_rows = 100  # Number of rows to insert into the hosts table

        for _ in range(num_rows):
            hostname = random_hostname()
            duration = 15

            # Insert a row into the hosts table (using INSERT OR IGNORE to prevent duplicates)
            await db.execute(
                "INSERT OR IGNORE INTO hosts (hostname, duration) VALUES (?, ?)",
                (hostname, duration),
            )

            # Insert random records for this hostname
            for _ in range(random.randint(1, 5)):  # Each hostname can have between 1 and 5 records
                record_datetime = random_datetime()
                await db.execute(
                    "INSERT INTO records (hostname, datetime) VALUES (?, ?)",
                    (hostname, record_datetime),
                )

        await db.commit()

# Run the insertion task
async def main():
    await insert_random_data()

# Execute the script
if __name__ == "__main__":
    asyncio.run(main())

