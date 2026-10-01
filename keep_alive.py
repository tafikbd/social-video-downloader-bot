import requests, threading, time, os

def ping_service():
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
    while True:
        try:
            requests.get(url)
            print(f"Pinged {url}")
        except Exception as e:
            print(f"Ping error: {e}")
        time.sleep(14 * 60)  # ১৪ মিনিট পরপর পিং

def start_keep_alive():
    thread = threading.Thread(target=ping_service)
    thread.daemon = True
    thread.start()
