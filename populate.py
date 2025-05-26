import sqlite3
import requests
from datetime import datetime
import ipaddress

API_URL = "https://fr.wikipedia.org/w/api.php"

def init_db(db_path):
    """Create tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        is_ip INTEGER,
        is_bot INTEGER,
        is_blocked INTEGER,
        UNIQUE(username)
    );

    CREATE TABLE IF NOT EXISTS revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id INTEGER UNIQUE,
        article_id INTEGER,
        user_id INTEGER,
        timestamp TEXT,
        comment TEXT,
        parent_id INTEGER,
        FOREIGN KEY(article_id) REFERENCES articles(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
                         
    CREATE TABLE IF NOT EXISTS scheduled_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE NOT NULL,
        interval_hours INTEGER DEFAULT 24,
        last_populated TEXT,
        is_active INTEGER DEFAULT 1
    );
    """)

    conn.commit()
    conn.close()

def fetch_revisions_from_api(title):
    """Fetch all revisions of a Wikipedia article using the MediaWiki API."""
    session = requests.Session()
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": title,
        "rvlimit": "max",
        "rvprop": "ids|timestamp|user|comment|flags",
        "formatversion": "2",
        "format": "json",
        "continue": ""
    }

    revisions = []
    total_fetched = 0

    while True:
        print("[fetch_revisions] Envoi de la requête API…")
        resp = session.get(API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        pages = data.get("query", {}).get("pages", [])
        if not pages or "revisions" not in pages[0]:
            break

        for rev in pages[0]["revisions"]:
            raw_timestamp = rev.get("timestamp")
            clean_timestamp = raw_timestamp.replace('T', ' ').replace('Z', '') if raw_timestamp else None
            revisions.append({
                "revision_id": rev.get("revid"),
                "parent_id": rev.get("parentid"),
                "timestamp": clean_timestamp,
                "user": rev.get("user"),
                "comment": rev.get("comment", ""),
                'is_bot': 'bot' in rev.get('groups', [])
            })
            total_fetched += 1

        if 'continue' in data:
            print("[fetch_revisions] Suite paginée, chargement…")
            params.update(data['continue'])
        else:
            break

    print(f"[fetch_revisions] Récupérées : {len(revisions)} révisions.")
    return revisions

def get_user_info(username):
    """Récupère bot/ip/bloqué via API ou détection IP"""
    print(f"[get_user_info] Analyse de {username}")

    api_url = "https://fr.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'list': 'users',
        'ususers': username,
        'usprop': 'groups|blockinfo',
        'format': 'json'
    }
    resp = requests.get(api_url, params=params, timeout=10)
    resp.raise_for_status()
    user = resp.json().get('query', {}).get('users', [{}])[0]

    # utilisateurs manquants ou invalides
    if user.get('missing') or user.get('invalid'):
        return {'is_ip': False, 'is_bot': False, 'is_blocked': False}
    
    # détection IP
    try:
        ipaddress.ip_address(username)
        return {'is_ip': True, 'is_bot': False, 'is_blocked': 'blockid' in user}
    except ValueError:
        pass

    return {
        'is_ip': False,
        'is_bot': 'bot' in user.get('groups', [])or 'bot' == username[-3:].lower(),
        'is_blocked': 'blockid' in user
    }

def update_database(conn, article_title, revisions):
    """Insert article and revision data into the database."""
    cur = conn.cursor()

    # Insert or ignore article
    cur.execute("INSERT OR IGNORE INTO articles (title) VALUES (?)", (article_title,))
    conn.commit()
    cur.execute("SELECT id FROM articles WHERE title = ?", (article_title,))
    article_id = cur.fetchone()["id"]

    for rev in revisions:
        username = rev["user"]
        user_info = get_user_info(username)
        
        # Insert or ignore user
        cur.execute("""
            INSERT OR IGNORE INTO users (username, is_ip, is_bot, is_blocked)
            VALUES (?, ?, ?, ?)
        """, (
            username, 
            int(user_info['is_ip']), 
            int(user_info['is_bot']), 
            int(user_info.get('is_blocked', False))
        ))

        conn.commit()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        user_id = cur.fetchone()["id"]

        # Insert revision
        cur.execute("""
            INSERT OR IGNORE INTO revisions
            (revision_id, article_id, user_id, timestamp, comment, parent_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            rev["revision_id"], article_id, user_id, rev["timestamp"],
            rev["comment"], rev.get("parent_id")
        ))

    conn.commit()