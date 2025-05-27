from flask import Flask, render_template, request, url_for, redirect, flash, session
from flask_bcrypt import Bcrypt
from functools import wraps
import sqlite3
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from populate import init_db, fetch_revisions_from_api, update_database
from queries import count_users, fetch_users, fetch_revisions_db, fetch_articles

app = Flask(__name__)
app.secret_key = 'L0n6 D4y!'
DB_PATH = "wikipedia.db"
bcrypt = Bcrypt(app)

def get_conn():
    """Get database connection with error handling"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        flash(f"Database error: {str(e)}", "error")
        return None

def check_scheduled_population():
    """Check for articles that need to be populated based on schedule"""
    conn = get_conn()
    try:
        articles = conn.execute("""
            SELECT title FROM scheduled_articles 
            WHERE is_active = 1 AND (
                last_populated IS NULL OR 
                datetime(last_populated, '+' || interval_hours || ' hours') < datetime('now')
            )
        """).fetchall()
        
        for article in articles:
            try:
                populate_article_now(article['title'])
                print(f"Populated {article['title']} on schedule")
            except Exception as e:
                print(f"Error populating {article['title']}: {str(e)}")
    finally:
        conn.close()

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(check_scheduled_population, 'interval', minutes=60)
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page', 'error')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
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

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form.get('email', '')

        if not username or not password:
            flash('Username and password are required', 'error')
            return redirect(url_for('register'))

        conn = get_conn()
        try:
            hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
            conn.execute(
                "INSERT INTO auth_users (username, password, email) VALUES (?, ?, ?)",
                (username, hashed_pw, email)
            )
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(e, 'error')
        finally:
            conn.close()

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_conn()
        try:
            user = conn.execute(
                "SELECT * FROM auth_users WHERE username = ?",
                (username,)
            ).fetchone()

            if user and bcrypt.check_password_hash(user['password'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                flash('Login successful!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
            else:
                flash('Invalid username or password', 'error')
        finally:
            conn.close()

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('index'))

@app.route('/articles')
@login_required
def articles():
    """Article list with precisions"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    try:
        conn = get_conn()
        cursor = conn.cursor()
        articles = fetch_articles(conn, limit=per_page, page=page)  # Pass conn to fetch_articles
        cursor.execute("SELECT COUNT(*) FROM articles")
        total = cursor.fetchone()[0]
        return render_template('articles_details.html',
                            articles=articles,
                            page=page,
                            per_page=per_page,
                            total=total)
    except Exception as e:
        flash(f"Error loading articles: {str(e)}", "error")
        return render_template('articles_details.html', articles=[])
    finally:
        if conn: conn.close()

@app.route('/articles/<title>')
@login_required
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
@login_required
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
            'active_days': request.args.get('active_days', type=int),
            'sort': request.args.get('sort', 'contributions') 
        }
        
        users = fetch_users(
            conn,
            article_title=filters['article'],
            bots_only=filters['bots'] == '1',
            ips_only=filters['ips'] == '1',
            blocked_only=filters['blocked'] == '1',
            active_within_days=filters['active_days'],
            limit=per_page,
            page=page,
            sort=filters['sort']
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
@login_required
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
@login_required
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

@app.route('/populate/schedule', methods=['GET', 'POST'])
@login_required
def populate_schedule():
    conn = get_conn()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'add':
                title = request.form.get('title').strip()
                interval = int(request.form.get('interval', 24))
                conn.execute(
                    "INSERT OR REPLACE INTO scheduled_articles (title, interval_hours) VALUES (?, ?)",
                    (title, interval)
                )
                conn.commit()
                flash(f"Added {title} to scheduled population", "success")
            elif action == 'remove':
                title = request.form.get('title')
                conn.execute("DELETE FROM scheduled_articles WHERE title = ?", (title,))
                conn.commit()
                flash(f"Removed {title} from scheduled population", "success")
            elif action == 'toggle':
                title = request.form.get('title')
                conn.execute(
                    "UPDATE scheduled_articles SET is_active = NOT is_active WHERE title = ?",
                    (title,)
                )
                conn.commit()
                flash("Schedule status updated", "success")
            elif action == 'run_now':
                title = request.form.get('title')
                populate_article_now(title)
                flash(f"Manually populated {title}", "success")

        # Get all scheduled articles
        scheduled = conn.execute(
            "SELECT *, datetime(last_populated) as last_populated_pretty FROM scheduled_articles"
        ).fetchall()
        
        return render_template('populate_schedule.html', scheduled=scheduled)
    finally:
        conn.close()

def populate_article_now(title):
    """Helper function to populate an article immediately"""
    conn = get_conn()
    try:
        revisions = fetch_revisions_from_api(title)
        update_database(conn, title, revisions)
        conn.execute(
            "UPDATE scheduled_articles SET last_populated = datetime('now') WHERE title = ?",
            (title,)
        )
        conn.commit()
    finally:
        conn.close()

@app.route('/search')
@login_required
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