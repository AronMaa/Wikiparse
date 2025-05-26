from flask import Flask, render_template, request, url_for, redirect, flash
import sqlite3
from datetime import datetime
from populate import init_db, fetch_revisions_from_api, update_database
from queries import count_users, fetch_users, fetch_revisions_db

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # In production, use proper secret management
DB_PATH = "wikipedia.db"

def get_conn():
    """Get database connection with error handling"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "error")
        return None

@app.route('/')
def index():
    """Homepage with article list"""
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT a.title, COUNT(r.id) AS rev_count
            FROM articles a
            LEFT JOIN revisions r ON r.article_id = a.id
            GROUP BY a.id;
        ''')
        articles = cursor.fetchall()
        return render_template('articles.html', articles=articles)
    except Exception as e:
        flash(f"Error loading articles: {str(e)}", "error")
        return render_template('articles.html', articles=[])
    finally:
        if conn: conn.close()

@app.route('/article/<title>')
def article_detail(title):
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    try:
        conn = get_conn()
        revisions = fetch_revisions_db(conn, article_title=title, limit=per_page, page=page)
        # Get total count for pagination
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM revisions r
            JOIN articles a ON a.id = r.article_id
            WHERE a.title = ?
        """, (title,))
        total = cur.fetchone()[0]
        return render_template('article_revisions.html', 
                             title=title, 
                             revisions=revisions,
                             page=page,
                             per_page=per_page,
                             total=total)
    except Exception as e:
        flash(f"Error loading revisions: {str(e)}", "error")
        return render_template('article_revisions.html', title=title, revisions=[])
    finally:
        if conn: conn.close()

@app.route('/users')
def users_list():
    """Filterable user list with pagination"""
    try:
        conn = get_conn()
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        
        # Collect all filters
        filters = {
            'article': request.args.get('article'),
            'bots': request.args.get('bots'),
            'ips': request.args.get('ips'),
            'blocked': request.args.get('blocked'),
            'active_days': request.args.get('active_days', type=int)
        }
        
        users = fetch_users(
            conn,
            article_title=filters['article'],
            bots_only=filters['bots'] == '1',
            ips_only=filters['ips'] == '1',
            blocked_only=filters['blocked'] == '1',
            active_within_days=filters['active_days'],
            limit=per_page,
            page=page
        )
        
        total = count_users(
            conn,
            article_title=filters['article'],
            bots_only=filters['bots'] == '1',
            ips_only=filters['ips'] == '1',
            blocked_only=filters['blocked'] == '1',
            active_within_days=filters['active_days']
        )
        
        return render_template('users.html', 
                            users=users, 
                            filters=filters,
                            pagination={
                                'page': page,
                                'per_page': per_page,
                                'total': total
                            })
    except Exception as e:
        flash(f"Error loading users: {str(e)}", "error")
        return render_template('users.html', 
                            users=[], 
                            filters=request.args,
                            pagination={
                                'page': 1,
                                'per_page': 25,
                                'total': 0
                            })
    finally:
        if conn: conn.close()

@app.route('/users/<username>')
def user_infos(username):    
    try:
        conn = get_conn()
        cursor = conn.cursor()
        
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        offset = (page - 1) * per_page
        
        # Get user information
        cursor.execute("""
            SELECT id, username, is_ip, is_bot, is_blocked 
            FROM users 
            WHERE username = ?
        """, (username,))
        user = cursor.fetchone()
        
        if not user:
            flash(f"Utilisateur {username} non trouvé", "error")
            return redirect(url_for('index'))
        
        # Get user revisions with pagination
        cursor.execute("""
            SELECT r.revision_id, r.parent_id, r.timestamp, r.comment, 
                   a.title as article_title, u.username
            FROM revisions r
            JOIN articles a ON a.id = r.article_id
            JOIN users u ON u.id = r.user_id
            WHERE u.username = ?
            ORDER BY r.timestamp DESC
            LIMIT ? OFFSET ?
        """, (username, per_page, offset))
        revisions = [dict(row) for row in cursor.fetchall()]
        
        # Get total count
        cursor.execute("""
            SELECT COUNT(*) 
            FROM revisions r
            JOIN users u ON u.id = r.user_id
            WHERE u.username = ?
        """, (username,))
        total = cursor.fetchone()[0]
        
        # Get user statistics
        cursor.execute("""
            SELECT COUNT(DISTINCT article_id) as edited_articles_count,
                   MIN(timestamp) as first_edit,
                   MAX(timestamp) as last_edit
            FROM revisions
            WHERE user_id = ?
        """, (user['id'],))
        stats = dict(cursor.fetchone())
        
        return render_template('user_revisions.html', 
                            user=dict(user),
                            revisions=revisions,
                            stats=stats,
                            total=total,
                            page=page,
                            per_page=per_page)
        
    except Exception as e:
        flash(f"Erreur lors du chargement des contributions: {str(e)}", "error")
        default_stats = {
            'edited_articles_count': 0,
            'first_edit': None,
            'last_edit': None
        }
        return render_template('user_revisions.html', 
                            user={'username': username},
                            revisions=[],
                            stats=default_stats,
                            total=0)
    finally:
        if conn: conn.close()
        
@app.route('/populate', methods=['GET', 'POST'])
def populate_db():
    """Database population endpoint"""
    if request.method == 'POST':
        try:
            article = request.form.get('article', '').strip()
            if not article:
                raise ValueError("Please enter an article title")
            
            if len(article) > 200:
                raise ValueError("Article title too long (max 200 chars)")

            conn = get_conn()
            init_db(DB_PATH)
            
            revisions = fetch_revisions_from_api(article)
            update_database(conn, article, revisions)
            
            flash(f"Successfully added {len(revisions)} revisions for {article}!", "success")
            return redirect(url_for('article_detail', title=article))
            
        except Exception as e:
            flash(f"Population error: {str(e)}", "error")
            return redirect(url_for('populate_db'))
    
    return render_template('populate.html')

@app.route('/search')
def search():
    query = request.args.get('q', '').strip().lower()
    if not query:
        flash("Veuillez entrer une requête de recherche.", "error")
        return redirect(url_for('index'))

    try:
        conn = get_conn()
        cursor = conn.cursor()

        # Search articles
        cursor.execute('''
            SELECT title FROM articles
            WHERE LOWER(title) LIKE ?
        ''', (f"%{query}%",))
        articles = cursor.fetchall()

        # Search users
        cursor.execute('''
            SELECT * FROM users
            WHERE LOWER(username) LIKE ?
        ''', (f"%{query}%",))
        users = cursor.fetchall()

        return render_template('search_results.html', query=query, articles=articles, users=users)

    except Exception as e:
        flash(f"Erreur de recherche : {str(e)}", "error")
        return redirect(url_for('index'))
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    init_db(DB_PATH)
    app.run(debug=True)