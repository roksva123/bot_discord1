from flask import Flask
from threading import Thread
import os
import logging

# Matikan log Flask yang berisik agar console lebih bersih
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask('')

@app.route('/')
def home():
    return "Bot is online!"

def run():
  # Gunakan port 8080 sebagai default (lebih standar untuk Replit/Glitch)
  port = int(os.environ.get('PORT', 8080))
  print(f"🌍 Web Server berjalan di Port {port}!")
  print(f"👉 Jika di Laptop: Buka http://localhost:{port} di browser untuk cek.")
  print(f"👉 Jika di Cloud: Masukkan URL publik ke UptimeRobot.")
  app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True # Penting: Agar web server ikut mati jika bot crash (supaya UptimeRobot tahu)
    t.start()