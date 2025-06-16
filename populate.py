import sqlite3
import requests
import re
import ipaddress
from datetime import datetime, timedelta

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
        user_id INTEGER,
        username TEXT,
        is_ip INTEGER,
        is_bot INTEGER,
        is_blocked INTEGER,
        is_scraped INTEGER DEFAULT 0,
        last_updated TEXT,
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
        flags TEXT,
        size_change INTEGER,
        tags TEXT,
        is_scraped INTEGER DEFAULT 0,
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
                         
    CREATE TABLE IF NOT EXISTS auth_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_approved BOOLEAN DEFAULT 0,
        is_admin BOOLEAN DEFAULT 0
    );
    
    CREATE INDEX IF NOT EXISTS index_users_scraped ON users(is_scraped);
    CREATE INDEX IF NOT EXISTS index_revisions_scraped ON revisions(is_scraped);
    CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
    """)

    conn.commit()
    conn.close()

def validate_wiki_title(title):
    """
    Validate Wikipedia article title according to Wikipedia's rules.
    Returns cleaned title or None if invalid.
    """
    if not title or not isinstance(title, str):
        return None
        
    # Remove leading/trailing whitespace
    title = title.strip()
    
    # Wikipedia titles can't be empty, start with lowercase, or contain certain special chars
    if not title or title[0].islower():
        return None
        
    # Basic validation - allow letters, numbers, spaces, punctuation and some special chars
    # This regex matches most valid Wikipedia titles while preventing injection
    if not re.match(r'^[A-Za-z0-9 _\-À-ÿ\'"(),.!?&%$€£§°+/:;=@#*\[\]\{\}]+$', title):
        return None
        
    return title

def fetch_revisions_from_api(title):
    """Fetch all revisions of a Wikipedia article using the MediaWiki API."""
    clean_title = validate_wiki_title(title)
    if not clean_title:
        print(f"[fetch_revisions] Invalid title: {title}")
        return []
    
    session = requests.Session()
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": clean_title,
        "rvlimit": "max",
        "rvprop": "ids|timestamp|user|comment|flags|size|tags",
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

        for i, rev in enumerate(pages[0]["revisions"]):
            raw_timestamp = rev.get("timestamp")
            clean_timestamp = raw_timestamp.replace('T', ' ').replace('Z', '') if raw_timestamp else None
            parent_size = None
            if i < len(pages[0]["revisions"]) - 1:
                parent_size = pages[0]["revisions"][i + 1]["size"]
            revisions.append({
                "revision_id": rev.get("revid"),
                "parent_id": rev.get("parentid"),
                "timestamp": clean_timestamp,
                "user": rev.get("user"),
                "comment": rev.get("comment", ""),
                'is_bot': 'bot' in rev.get('groups', []),
                "flags": ','.join(rev.get('flags', [])),
                "size": rev['size'],  # Taille actuelle
                "parent_size": parent_size,  # Taille de la version précédente
                "size_change": rev['size'] - parent_size if parent_size is not None else None,  # Différence de taille
                "tags": ','.join(rev.get('tags', []))
            })
            total_fetched += 1

        if 'continue' in data:
            print("[fetch_revisions] Suite paginée, chargement…")
            params.update(data['continue'])
        else:
            break

    print(f"[fetch_revisions] Récupérées : {len(revisions)} révisions.")
    return revisions

def rescrape_users(conn):
    """Rescrape all users older than 7 days"""
    one_week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    
    users_to_rescrape = conn.execute("""
        SELECT username FROM users 
        WHERE last_updated IS NULL OR last_updated < ?
        ORDER BY last_updated ASC NULLS FIRST
    """, (one_week_ago,)).fetchall()
    
    for user in users_to_rescrape:
        username = user['username']
        print(f"[rescrape_users] Mise à jour des informations pour {username}")
        
        user_info = get_user_info(username)
        now = datetime.now().isoformat()
        
        conn.execute("""
            UPDATE users 
            SET is_bot = ?,
                is_ip = ?,
                is_blocked = ?,
                user_id = ?,
                last_updated = ?,
                is_scraped = 1
            WHERE username = ?
        """, (
            int(user_info.get('is_bot', False)),
            int(user_info.get('is_ip', False)),
            int(user_info.get('is_blocked', False)),
            user_info.get('user_id'),
            now,
            username
        ))
    
    conn.commit()
    return len(users_to_rescrape)

def get_user_info(username):
    """Récupère bot/ip/bloqué via API ou détection IP"""
    print(f"[get_user_info] Analyse de {username}")

    try:
        ipaddress.ip_address(username)
        return {'is_ip': True, 'is_bot': False, 'is_blocked': False, 'user_id': None}
    except ValueError:
        pass
    
    # Handle None or empty username
    if not username:
        return {'is_ip': False, 'is_bot': False, 'is_blocked': False, 'user_id': None}

    api_url = "https://fr.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'list': 'users',
        'ususers': username,
        'usprop': 'groups|blockinfo|userid',
        'format': 'json'
    }

    try:
        resp = requests.get(api_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # Handle case where API returns no users
        users = data.get('query', {}).get('users', [])
        if not users:
            return {'is_ip': False, 'is_bot': False, 'is_blocked': False, 'user_id': None}
            
        user = users[0]
        
        # Handle missing or invalid users
        if user.get('missing') or user.get('invalid'):
            return {'is_ip': False, 'is_bot': False, 'is_blocked': False, 'user_id': None}
        
        return {
            'is_ip': False,
            'is_bot': 'bot' in user.get('groups', []) or username.lower().endswith('bot'),
            'is_blocked': 'blockid' in user,
            'user_id': user.get('userid')
        }
        
    except Exception as e:
        print(f"[get_user_info] Error fetching user info: {str(e)}")
        return {'is_ip': False, 'is_bot': False, 'is_blocked': False, 'user_id': None}
    
def update_database(conn, article_title, revisions):
    """
    Insert article and revision data into the database.
    Optimized to skip scraping user details if already marked as scraped.
    """
    cur = conn.cursor()

    # Insert or ignore article
    cur.execute("INSERT OR IGNORE INTO articles (title) VALUES (?)", (article_title,))
    cur.execute("SELECT id FROM articles WHERE title = ?", (article_title,))
    article_id_row = cur.fetchone()

    if not article_id_row:
        print(f"[update_database] Error: Could not get article_id for '{article_title}'. Aborting update for this article.")
        return
    article_id = article_id_row["id"]

    users_api_called_count = 0
    users_skipped_api_count = 0
    revisions_inserted_count = 0
    revisions_ignored_count = 0

    for rev in revisions:
        username = rev.get("user")
        if not username:  # Skip if no username
            print(f"[update_database] Skipping revision_id {rev.get('revision_id')} due to missing username.")
            continue
            
        user_id = None # This is the local DB user.id
        
        # Check if user exists and is already scraped
        cur.execute("SELECT id, is_scraped, last_updated FROM users WHERE username = ?", (username,))
        user_row = cur.fetchone()

        if user_row and user_row['is_scraped'] == 1 and user_row['last_updated']:
            last_updated = datetime.fromisoformat(user_row['last_updated'])
            if (datetime.now() - last_updated).days < 7:
                user_id = user_row['id']
                # print(f"[update_database] User '{username}' (ID: {user_id}) already scraped. Skipping API call.")
                users_skipped_api_count += 1
        else:
            # User not found, or found but not marked as scraped.
            # if user_row: # User exists but is_scraped = 0 or NULL
            # print(f"[update_database] User '{username}' (ID: {user_row['id']}) found but not scraped. Calling API.")
            # else: # User not found
            # print(f"[update_database] User '{username}' not found in DB. Calling API.")
            
            user_info = get_user_info(username) # Actual API call
            users_api_called_count +=1
            now = datetime.now().isoformat()
            
            # Insert new user or update existing user (if they weren't scraped before).
            # Set is_scraped = 1 in both cases.
            # Using standard "UPSERT" syntax.
            cur.execute("""
                INSERT INTO users (username, is_ip, is_bot, is_blocked, user_id, is_scraped, last_updated)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(username) DO UPDATE SET
                    is_ip = excluded.is_ip,
                    is_bot = excluded.is_bot,
                    is_blocked = excluded.is_blocked,
                    user_id = excluded.user_id,
                    is_scraped = 1,
                    last_updated = excluded.last_updated;
            """, (
                username, 
                int(user_info.get('is_ip', False)), 
                int(user_info.get('is_bot', False)), 
                int(user_info.get('is_blocked', False)),
                user_info.get('user_id'), # This can be None
                now
            ))
            
            # Fetch the user_id (local DB id) after insert/update
            # This is necessary because `ON CONFLICT DO UPDATE` might not return `lastrowid` predictably
            # for updates, and even for inserts, explicit select is safer.
            cur.execute("SELECT id FROM users WHERE username = ?", (username,))
            user_result = cur.fetchone()
            if user_result:
                user_id = user_result["id"]
            else:
                print(f"[update_database] CRITICAL: Failed to retrieve ID for user '{username}' after DB operation. Skipping revision.")
                continue
        
        if not user_id: # Should have been caught above, but as a final safeguard
            print(f"[update_database] Error: User ID for '{username}' could not be resolved. Skipping revision_id {rev.get('revision_id')}.")
            continue

        # Insert revision
        # INSERT OR IGNORE handles duplicate revision_ids (unique constraint on revision_id)
        cur.execute("""
            INSERT OR IGNORE INTO revisions
            (revision_id, article_id, user_id, timestamp, comment, parent_id, is_scraped, flags, size_change, tags)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (
            rev["revision_id"], article_id, user_id, rev["timestamp"],
            rev["comment"], rev.get("parent_id"),
            rev.get("flags"), rev.get("size_change"), rev.get("tags")
        ))

        if cur.rowcount > 0: # rowcount is 1 if a row was inserted, 0 if ignored
            revisions_inserted_count += 1
        else:
            revisions_ignored_count += 1

    conn.commit() # Commit once after processing all revisions for the article
    
    print(f"[update_database] Finished processing for article '{article_title}'.")
    print(f"[update_database] User API calls made: {users_api_called_count}. User API calls skipped: {users_skipped_api_count}.")
    print(f"[update_database] Revisions newly inserted: {revisions_inserted_count}. Revisions ignored (already exist): {revisions_ignored_count}.")