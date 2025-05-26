def fetch_revisions_db(conn, article_title, limit=25, page=1):
    offset = (page - 1) * limit
    cur = conn.cursor()
    cur.execute("""
        SELECT r.revision_id, r.parent_id, r.timestamp, r.comment, u.username
        FROM revisions r
        JOIN articles a ON a.id = r.article_id
        JOIN users u ON u.id = r.user_id
        WHERE a.title = ?
        ORDER BY r.timestamp DESC
        LIMIT ? OFFSET ?
    """, (article_title, limit, offset))
    return cur.fetchall()

def fetch_users(conn, article_title=None, bots_only=False, ips_only=False,
                blocked_only=False, active_within_days=None, limit=25, page=1, sort='contributions'):
    """Fetch filtered user list from DB."""
    offset = (page - 1) * limit
    params = []
    filters = []
    joins = ""
    order_by = ""

    if article_title:
        joins += """
            JOIN revisions r ON r.user_id = u.id
            JOIN articles a ON a.id = r.article_id
        """
        filters.append("a.title = ?")
        params.append(article_title)

    if bots_only:
        filters.append("u.is_bot = 1")
    if ips_only:
        filters.append("u.is_ip = 1")
    if blocked_only:
        filters.append("u.is_blocked = 1")

    if active_within_days:
        joins += " JOIN revisions rev2 ON rev2.user_id = u.id"
        filters.append("rev2.timestamp >= datetime('now', ?)")
        params.append(f"-{active_within_days} days")
    
    # Add sorting logic
    if sort == 'contributions':
        order_by = "ORDER BY contributions DESC"
    elif sort == 'newest':
        order_by = "ORDER BY last_edit DESC"
        joins += " LEFT JOIN (SELECT user_id, MAX(timestamp) as last_edit FROM revisions GROUP BY user_id) le ON le.user_id = u.id"
    elif sort == 'oldest':
        order_by = "ORDER BY first_edit ASC"
        joins += " LEFT JOIN (SELECT user_id, MIN(timestamp) as first_edit FROM revisions GROUP BY user_id) fe ON fe.user_id = u.id"
    
    params.extend([limit, offset])

    query = f"""
            SELECT u.id, u.username, u.is_ip, u.is_bot, u.is_blocked, 
                COUNT(DISTINCT rev.id) AS contributions
            FROM users u
            {joins}
            LEFT JOIN revisions rev ON rev.user_id = u.id
            {f"AND rev.article_id = a.id" if article_title else ""}
            {f"WHERE {' AND '.join(filters)}" if filters else ""}
            GROUP BY u.id
            {order_by}
            LIMIT ? OFFSET ?
            """

    cur = conn.cursor()
    return cur.execute(query, params).fetchall()

def count_users(conn, article_title=None, bots_only=False, ips_only=False,
               blocked_only=False, active_within_days=None):
    """Count total users for pagination"""
    params = []
    filters = []
    joins = ""

    # Same filter logic as fetch_users
    if article_title:
        joins += """
            JOIN revisions r ON r.user_id = u.id
            JOIN articles a ON a.id = r.article_id
        """
        filters.append("a.title = ?")
        params.append(article_title)

    if bots_only:
        filters.append("u.is_bot = 1")
    if ips_only:
        filters.append("u.is_ip = 1")
    if blocked_only:
        filters.append("u.is_blocked = 1")

    if active_within_days:
        joins += " JOIN revisions r2 ON r2.user_id = u.id"
        filters.append("r2.timestamp >= datetime('now', ?)")
        params.append(f"-{active_within_days} days")

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    
    query = f"""
        SELECT COUNT(DISTINCT u.id)
        FROM users u
        {joins}
        {where_clause}
    """

    cur = conn.cursor()
    return cur.execute(query, params).fetchone()[0]

def fetch_articles(conn, limit=25, page=1):
    offset = (page - 1) * limit
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.title, 
               COUNT(r.id) AS nb_revisions,
               COUNT(DISTINCT r.user_id) AS nb_users,
               MAX(r.timestamp) AS last_change
        FROM articles a
        LEFT JOIN revisions r ON r.article_id = a.id
        GROUP BY a.id
        ORDER BY last_change DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    return cur.fetchall()