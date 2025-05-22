import sqlite3
import requests
from datetime import datetime

def init_db(db_path):
    """Initialise la base de données"""
    print(f"[init_db] Initialisation de la base de données : {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("[init_db] Création des tables si elles n'existent pas...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            title TEXT UNIQUE,
            last_fetched TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            is_ip BOOLEAN,
            is_bot BOOLEAN,
            is_blocked BOOLEAN,
            last_checked TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS revisions (
            id INTEGER PRIMARY KEY,
            article_id INTEGER,
            user_id INTEGER,
            timestamp TIMESTAMP,
            content_diff TEXT,
            comment TEXT,
            FOREIGN KEY(article_id) REFERENCES articles(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    print("[init_db] Tables créées et base de données prête.")
    return conn

def fetch_revisions_from_api(article_title):
    """Récupère toutes les révisions (API Wikipédia)"""
    print(f"[fetch_revisions] Démarrage pour : {article_title}")
    api_url = "https://fr.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'prop': 'revisions',
        'titles': article_title,
        'rvprop': 'user|timestamp|comment|content',
        'rvlimit': 'max',
        'rvslots': 'main',
        'format': 'json'
    }
    session = requests.Session()
    revisions = []

    while True:
        print("[fetch_revisions] Envoi de la requête API…")
        resp = session.get(api_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        for page in data['query']['pages'].values():
            revisions.extend(page.get('revisions', []))
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
    # détection IP
    try:
        import ipaddress
        ipaddress.ip_address(username)
        return {'is_ip': True, 'is_bot': False, 'is_blocked': False}
    except ValueError:
        pass

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

    return {
        'is_ip': False,
        'is_bot': 'bot' in user.get('groups', []),
        'is_blocked': 'blockid' in user
    }

def update_database(conn, article_title, revisions):
    cursor = conn.cursor()
    
    # Insert article
    cursor.execute('''
        INSERT OR IGNORE INTO articles (title, last_fetched)
        VALUES (?, ?)
    ''', (article_title, datetime.now()))
    
    # Get article ID
    article_id = cursor.execute(
        'SELECT id FROM articles WHERE title = ?', (article_title,)
    ).fetchone()[0]

    for i, rev in enumerate(revisions, start=1):
        user = rev['user']
        ts = datetime.strptime(rev['timestamp'], '%Y-%m-%dT%H:%M:%SZ')
        info = get_user_info(user)
        print(f"[update_db] Rév#{i} by {user} → {info}")
        
        # Use INSERT OR REPLACE to handle updates
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (id, username, is_ip, is_bot, is_blocked, last_checked)
            VALUES (
                (SELECT id FROM users WHERE username = ?),
                ?, ?, ?, ?, ?
            )
        ''', (user, user, *get_user_info(user).values(), datetime.now()))
        
        # Get user ID
        user_id = cursor.execute(
            'SELECT id FROM users WHERE username = ?', (user,)
        ).fetchone()[0]

        # revisions
        content = rev.get('slots', {}) \
                     .get('main', {}) \
                     .get('content', '')
        comment = rev.get('comment', '')
        cursor.execute('''
            INSERT INTO revisions
            (article_id, user_id, timestamp, content_diff, comment)
            VALUES (?, ?, ?, ?, ?)
        ''', (article_id, user_id, ts, content, comment))

    conn.commit()
    print("[update_db] Terminé.")

if __name__ == '__main__':
    conn = init_db("wikipedia.db")
    revs = fetch_revisions_from_api("Israël")
    update_database(conn, "Israël", revs)
    conn.close()