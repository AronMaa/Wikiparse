from datetime import datetime, timedelta
import sqlite3
from typing import Optional, List, Dict, Union

def fetch_revisions_db(conn: sqlite3.Connection, 
                      *, 
                      article_title: Optional[str] = None, 
                      limit: Optional[int] = 100) -> List[Dict]:
    """
    Fetch revisions with safety limits and type hints
    """
    cursor = conn.cursor()
    query = '''
        SELECT r.id, a.title, u.username, r.timestamp, r.comment, r.content_diff
        FROM revisions r
        JOIN users u ON u.id = r.user_id
        JOIN articles a ON a.id = r.article_id
    '''
    params = []
    
    if article_title:
        query += " WHERE a.title = ?"
        params.append(article_title[:200])  # Prevent long title DOS
    
    query += " ORDER BY r.timestamp DESC LIMIT ?"
    params.append(min(limit, 1000) if limit else 100)  # Max 1000 results
    
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]

def fetch_users(conn: sqlite3.Connection, 
              *, 
              article_title: Optional[str] = None,
              bots_only: bool = False,
              ips_only: bool = False,
              blocked_only: bool = False,
              active_within_days: Optional[int] = None) -> List[Dict]:
    """
    User query with parameter sanitization
    """
    cursor = conn.cursor()
    conditions = []
    params = []
    
    # Input sanitization
    if article_title:
        conditions.append("a.title = ?")
        params.append(article_title[:200])
    
    if bots_only: conditions.append("u.is_bot = 1")
    if ips_only: conditions.append("u.is_ip = 1")
    if blocked_only: conditions.append("u.is_blocked = 1")
    
    if active_within_days:
        try:
            cutoff = datetime.now() - timedelta(days=min(active_within_days, 365))
            conditions.append("u.last_checked >= ?")
            params.append(cutoff)
        except:
            pass  # Fail-safe for invalid days input
    
    query = '''
        SELECT u.id, u.username, u.is_ip, u.is_bot, u.is_blocked, 
               u.last_checked, COUNT(r.id) as contributions
        FROM users u
        LEFT JOIN revisions r ON r.user_id = u.id
        LEFT JOIN articles a ON a.id = r.article_id
    '''
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " GROUP BY u.id ORDER BY contributions DESC LIMIT 500;"  # Max 500 users
    
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]
