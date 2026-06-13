from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run():
    # Render userà la variabile d'ambiente PORT (solitamente diversa da 8080)
    # Impostiamo 0.0.0.0 per ascoltare su tutti gli IP pubblici
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
