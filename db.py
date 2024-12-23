# db.py

import sqlite3

# Nom du fichier SQLite (un simple fichier sur disque)
DB_NAME = "bot_data.db"

def init_db():
    """
    Crée (ou ouvre) la base de données SQLite et initialise les tables nécessaires.
    Retourne la connexion conn que vous utiliserez ensuite pour lire/écrire.
    """
    # Crée ou ouvre le fichier 'bot_data.db'
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Table pour stocker les tweets déjà vus/traités
    # 'tweet_id' sera la PRIMARY KEY
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_tweets (
            tweet_id TEXT PRIMARY KEY
        )
    """)

    # Table pour stocker les utilisateurs à qui on a déjà envoyé un DM
    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_sent_users (
            user_id TEXT PRIMARY KEY
        )
    """)

    # Ici, vous pouvez ajouter d'autres tables si nécessaire, par exemple :
    # c.execute("CREATE TABLE IF NOT EXISTS some_other_table (...);")

    # Valider la création
    conn.commit()

    return conn

def mark_tweet_seen(conn, tweet_id):
    """
    Enregistre le tweet_id dans la table seen_tweets, pour éviter de le re-traiter.
    Utilise INSERT OR IGNORE pour ne pas dupliquer si déjà existant.
    """
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO seen_tweets (tweet_id) VALUES (?)", (tweet_id,))
    conn.commit()

def is_tweet_seen(conn, tweet_id):
    """
    Vérifie si le tweet_id est déjà présent dans la table seen_tweets.
    Retourne True si déjà vu, False sinon.
    """
    c = conn.cursor()
    c.execute("SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (tweet_id,))
    row = c.fetchone()
    return (row is not None)

def mark_user_dm(conn, user_id):
    """
    Enregistre le user_id dans la table dm_sent_users, pour ne pas DM deux fois la même personne.
    """
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO dm_sent_users (user_id) VALUES (?)", (user_id,))
    conn.commit()

def has_user_dm(conn, user_id):
    """
    Vérifie si on a déjà DM ce user_id. Retourne True si déjà DM, False sinon.
    """
    c = conn.cursor()
    c.execute("SELECT 1 FROM dm_sent_users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return (row is not None)

def close_db(conn):
    """
    Ferme la connexion à la base de données proprement.
    À appeler par exemple à la fin de votre script.
    """
    conn.close()

