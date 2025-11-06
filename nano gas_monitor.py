from gpiozero import MCP3008
import time
from datetime import datetime
from openai import OpenAI
from twilio.rest import Client
import csv
import os

# ==== MACHINE IDENTIFICATION ====
MACHINE_NAME = "LabSensor-01"   # Change this for each Pi

# ==== OPENAI SETUP ====
client = OpenAI()

# ==== TWILIO SETUP ====
TWILIO_SID = "YOUR_TWILIO_SID"
TWILIO_AUTH_TOKEN = "YOUR_TWILIO_AUTH_TOKEN"
TWILIO_FROM = "+18777804236"    # Your Twilio number
TWILIO_TO = "+19495505587"      # Your phone number for alerts

twilio_client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

def send_sms(message):
    try:
        twilio_client.messages.create(
            body=message,
            from_=TWILIO_FROM,
            to=TWILIO_TO
        )
        print(f"📱 SMS sent: {message}")
    except Exception as e:
        print("Error sending SMS:", e)

# ==== LOGGING SETUP ====
def get_log_filename():
    date_str = datetime.now().strftime("%m-%d-%Y")
    return f"gas_log_{date_str}.csv"

def ensure_log_headers(filename):
    if not os.path.exists(filename):
        with open(filename, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow([
                "Machine Name", "Timestamp", "Raw Reading (0-1)",
                "Scaled Reading (0-1023)", "Deviation (%)", "AI Analysis"
            ])

def log_data(machine, timestamp, reading, scaled, deviation, analysis):
    filename = get_log_filename()
    ensure_log_headers(filename)
    with open(filename, mode="a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([machine, timestamp, reading, scaled, deviation, analysis])

def get_summary_filename():
    date_str = datetime.now().strftime("%m-%d-%Y")
    return f"summary_{date_str}.txt"

# ==== SENSOR SETUP ====
gas_sensor = MCP3008(channel=0)

print(f"[{MACHINE_NAME}] Warming up sensor...")
time.sleep(60)
print(f"[{MACHINE_NAME}] Warmup complete. Establishing baseline...")

baseline_samples = []
for _ in range(30):
    baseline_samples.append(gas_sensor.value)
    time.sleep(1)

baseline = sum(baseline_samples) / len(baseline_samples)
print(f"[{MACHINE_NAME}] Baseline established at {baseline:.4f}")

# ==== HELPER FUNCTIONS ====
def is_weekday():
    return datetime.now().weekday() < 5

def within_operating_hours():
    now = datetime.now()
    return 7 <= now.hour < 16

def time_for_summary():
    now = datetime.now()
    return now.hour == 16 and 30 <= now.minute < 35

def weekday_heartbeat_time():
    now = datetime.now()
    return now.weekday() < 5 and now.hour == 7 and 5 <= now.minute < 10

def summarize_day():
    filename = get_log_filename()
    if not os.path.exists(filename):
        print("No data to summarize for today.")
        return

    with open(filename, "r") as file:
        data = file.read()

    try:
        print("📊 Generating daily summary...")
        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": (
                    "You are an air quality monitoring assistant. "
                    "Summarize the day's gas sensor data concisely, "
                    "highlighting any unusual spikes or vapor detections."
                )},
                {"role": "user", "content": f"Here is today's data from {MACHINE_NAME}:\n\n{data}"}
            ]
        )

        summary = response.choices[0].message.content.strip()

        summary_file = get_summary_filename()
        with open(summary_file, "w") as f:
            f.write(f"Daily Summary for {MACHINE_NAME} on {datetime.now().strftime('%m-%d-%Y')}\n\n")
            f.write(summary)

        print(f"\n=== Daily Summary for {MACHINE_NAME} ===\n{summary}\n")

        send_sms(f"📋 {MACHINE_NAME} Summary:\n{summary[:1500]}")
    except Exception as e:
        print("Error generating or sending summary:", e)

# ==== MAIN LOOP ====
print(f"[{MACHINE_NAME}] Active Mon–Fri, 7:00 AM – 4:00 PM, summary at 4:30 PM.")
summary_sent = False
heartbeat_sent = False

while True:
    now = datetime.now()

    # --- Daily weekday heartbeat ---
    if weekday_heartbeat_time() and not heartbeat_sent:
        send_sms(f"✅ {MACHINE_NAME} Online — monitoring started for the day.")
        heartbeat_sent = True
        time.sleep(600)  # avoid duplicates
        continue
    elif now.hour == 0:
        heartbeat_sent = False  # reset flag at midnight

    # --- Only operate Monday–Friday ---
    if is_weekday():
        if within_operating_hours():
            timestamp = now.strftime("%m-%d-%Y at %H:%M.%S")
            reading = gas_sensor.value
            scaled_reading = reading * 1023
            deviation = (reading - baseline) / baseline * 100

            sensor_message = (
                f"Machine: {MACHINE_NAME}\n"
                f"Time: {timestamp}\n"
                f"Gas sensor reading (scaled): {scaled_reading:.2f}\n"
                f"Gas sensor reading (raw): {reading:.4f}\n"
                f"Deviation from baseline: {deviation:.2f}%"
            )

            try:
                response = client.chat.completions.create(
                    model="gpt-5",
                    messages=[
                        {"role": "system", "content": (
                            "You are an air quality monitoring assistant. "
                            "Analyze this single reading to determine if vapor or gas activity is likely. "
                            "Only report 'possible vapor event detected' if clear evidence exists."
                        )},
                        {"role": "user", "content": sensor_message}
                    ]
                )

                analysis = response.choices[0].message.content.strip()
                print("\n--- New Measurement ---")
                print(f"Machine: {MACHINE_NAME}")
                print(f"Time: {timestamp}")
                print("AI analysis:", analysis)

            except Exception as e:
                print("Error analyzing with OpenAI:", e)
                analysis = "Error during AI analysis"

            log_data(MACHINE_NAME, timestamp, f"{reading:.4f}", f"{scaled_reading:.2f}", f"{deviation:.2f}", analysis)

            if deviation > 30:
                alert_message = (
                    f"⚠️ ALERT from {MACHINE_NAME} at {timestamp}: "
                    f"Significant increase detected ({deviation:.2f}% over baseline)!"
                )
                print(alert_message)
                send_sms(alert_message)

            time.sleep(300)

        elif time_for_summary() and not summary_sent:
            summarize_day()
            summary_sent = True
            time.sleep(600)

        else:
            if now.hour == 0 and summary_sent:
                summary_sent = False
            time.sleep(600)

    else:
        print(f"[{MACHINE_NAME}] Weekend mode — sleeping until Monday.")
        time.sleep(3600)
