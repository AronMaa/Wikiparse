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
                blocked_only=False, active_within_days=None):
    """Fetch filtered user list from DB."""
    params = []
    filters = []
    joins = ""

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
        SELECT u.id, u.username, u.is_ip, u.is_bot, u.is_blocked, 
               COUNT(DISTINCT r.id) AS contributions
        FROM users u
        LEFT JOIN revisions r ON r.user_id = u.id
        {joins}
        {where_clause}
        GROUP BY u.id
        ORDER BY contributions DESC
        LIMIT 200
    """

    cur = conn.cursor()
    return cur.execute(query, params).fetchall()
