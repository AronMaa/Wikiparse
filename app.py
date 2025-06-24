from datetime import datetime
from flask import Flask, render_template, request, url_for, redirect, flash, session, jsonify
from flask_bcrypt import Bcrypt
from functools import wraps
import sqlite3
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from classifier import analyze_with_gpt, build_prompt_from_revisions, get_user_revisions_diff, analyze_top_contributors
from populate import init_db, fetch_revisions_from_api, update_database, rescrape_users
from queries import count_users, fetch_users, fetch_revisions_db, fetch_articles
from itsdangerous import URLSafeTimedSerializer

app = Flask(__name__)
app.secret_key = 'L0n6 D4y!'
DB_PATH = "wikipedia.db"
bcrypt = Bcrypt(app)

serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

def first_admin():
    """Ensure at least one admin exists in the system"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        # Check if any admin exists
        cursor.execute("SELECT id FROM auth_users WHERE is_admin = 1 LIMIT 1")
        admin_exists = cursor.fetchone()
        
        if not admin_exists:
            hashed_pw = bcrypt.generate_password_hash('Password123').decode('utf-8')
            cursor.execute("""
                INSERT OR IGNORE INTO auth_users (
                    username, password, is_approved, is_admin
                ) VALUES (?, ?, ?, ?)
                """, ('admin', hashed_pw, True, True))
            conn.commit()
            print("Created initial admin user")
    except sqlite3.Error as e:
        print(f"Database error during admin check: {str(e)}")
    finally:
        if conn:
            conn.close()

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

        # Rescrape old users
        rescrape_users(conn)

        # Analyse des top contributeurs
        if datetime.now().hour == 3:
            print("Analyzing top contributors...")
            results = analyze_top_contributors()
            print(f"Analyzed {len(results)} top contributors")
            
    finally:
        conn.close()

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(check_scheduled_population, 'interval', hours=1)
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

def approved_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_approved'):
            flash('Approved access required', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def classification_update(username, analysis):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    user_id = cursor.fetchone()
    
    if user_id:
        user_id = user_id[0]
        cursor.execute("""
            UPDATE users SET classification = (?) WHERE id = ?
        """, (analysis[0], user_id))
            
        conn.commit()

@app.route('/')
def index():
    """Homepage"""
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Validation
        if not all([username, password]):
            flash('All fields are required', 'error')
            return redirect(url_for('register'))

        conn = get_conn()
        try:
            # Check if any admin exists to approve users
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM auth_users WHERE is_approved = 1 LIMIT 1")
            admin_exists = cursor.fetchone()
            
            if not admin_exists:
                flash('System not ready for registrations - no admin available to approve accounts', 'error')
                return redirect(url_for('register'))

            hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
            cursor.execute(
                """INSERT INTO auth_users 
                (username, password, is_approved, is_admin) 
                VALUES (?, ?, ?, ?)""",
                (username, hashed_pw, 0, 0)
            )
            conn.commit()
            
            flash('Registration successful! Waiting for approval', 'success')
            return redirect(url_for('login'))
            
        except sqlite3.IntegrityError:
            conn.rollback()
            flash('Username already exists', 'error')
        except Exception as e:
            conn.rollback()
            flash(f'Registration error: {str(e)}', 'error')
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
                if not user['is_approved']:
                    flash('Your account is pending approval', 'error')
                    return redirect(url_for('login'))
                
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['is_admin'] = user['is_admin']
                session['is_approved'] = user['is_approved']
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

@app.route('/admin')
@login_required
@approved_required
@admin_required
def admin_dashboard():
    conn = get_conn()
    try:
        pending_users = conn.execute(
            "SELECT * FROM auth_users WHERE is_approved = 0"
        ).fetchall()
        all_users = conn.execute(
            "SELECT * FROM auth_users ORDER BY is_approved DESC, username"
        ).fetchall()
        articles = conn.execute("SELECT * FROM articles").fetchall()
        
        return render_template('admin.html', 
                             pending_users=pending_users,
                             all_users=all_users,
                             articles=articles)
    finally:
        conn.close()

@app.route('/admin/toggle-user/<int:user_id>')
@login_required
@approved_required
@admin_required
def toggle_user(user_id):
    conn = get_conn()
    try:
        # Check if user exists
        user = conn.execute(
            "SELECT * FROM auth_users WHERE id = ?", 
            (user_id,)
        ).fetchone()
        
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('admin_dashboard'))
            
        conn.execute(
            "UPDATE auth_users SET is_approved = NOT is_approved WHERE id = ?", 
            (user_id,)
        )
        conn.commit()
        action = "Granted Access to" if not user['is_approved'] else "Revoked Access to"
        action2 = "approved" if not user['is_approved'] else "rejected"
        flash(f'{action} {user["username"]}', 'success')
        flash(f'User {user["username"]} {action2}', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error updating user: {str(e)}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject-user/<int:user_id>')
@login_required
@approved_required
@admin_required
def reject_user(user_id):
    conn = get_conn()
    try:
        # Check if user exists
        user = conn.execute(
            "SELECT * FROM auth_users WHERE id = ?", 
            (user_id,)
        ).fetchone()
        
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('admin_dashboard'))
            
        conn.execute(
            "DELETE FROM auth_users WHERE id = ?", 
            (user_id,)
        )
        conn.commit()
        flash(f'User {user["username"]} rejected and removed', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error rejecting user: {str(e)}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/analytics')
@login_required
@approved_required
@admin_required
def admin_analytics():
    return render_template('admin_analytics.html')

@app.route('/admin/debug-db')
@login_required
@approved_required
@admin_required
def debug_database():
    try:
        conn = get_conn()
        # Attempt a basic write query to check for locks
        conn.execute("PRAGMA journal_mode = WAL;")  # Optional: improve concurrency
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("VACUUM;")                     # Optional: compact database
        conn.commit()
        flash("Database debug completed successfully. No locks detected.", "success")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            flash("Database is locked. Try again later or restart the app.", "error")
        else:
            flash(f"Database error: {str(e)}", "error")
    except Exception as e:
        flash(f"Unexpected error: {str(e)}", "error")
    finally:
        if conn:
            conn.close()

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle-admin/<int:user_id>')
@login_required
@approved_required
@admin_required
def toggle_admin(user_id):
    if user_id == session['user_id']:
        flash("You can't change your own admin status", 'error')
        return redirect(url_for('admin_dashboard'))
    
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT * FROM auth_users WHERE id = ?", 
            (user_id,)
        ).fetchone()
        
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('admin_dashboard'))
            
        if not user['is_approved']:
            flash('User must be approved first', 'error')
            return redirect(url_for('admin_dashboard'))
            
        conn.execute(
            "UPDATE auth_users SET is_admin = NOT is_admin WHERE id = ?",
            (user_id,)
        )
        conn.commit()
        action = "Granted Admin Privileges to" if not user['is_admin'] else "Revoked Admin Privileges from"
        flash(f'{action} {user["username"]}', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error updating admin status: {str(e)}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/rescrape-users')
@login_required
@approved_required
@admin_required
def admin_rescrape_users():
    try:
        conn = get_conn()
        count = rescrape_users(conn)
        flash(f"Rescraped {count} user accounts", "success")
    except Exception as e:
        flash(f"Error rescraping users: {str(e)}", "error")
    finally:
        if conn: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/articles')
@login_required
@approved_required
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
        return render_template('articles.html',
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
@approved_required
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
@approved_required
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
@approved_required
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
            SELECT id, username, is_ip, is_bot, is_blocked, classification 
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
@approved_required
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
@approved_required
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
@approved_required
def search():
    query = request.args.get('q', '').strip()
    if not query:
        flash("Please enter a search query", "error")
        return redirect(url_for('index'))

    try:        
        # Use the API endpoint to get consistent results
        api_url = url_for('api_search', q=query, _external=False)
        response = app.test_client().get(api_url)
        
        if response.status_code != 200:
            flash("Search service unavailable", "error")
            return redirect(url_for('index'))
            
        data = response.get_json()
    
        articles = data.get('articles', [])
        users = data.get('users', [])
        
        return render_template('search_results.html', 
                            query=query, 
                            articles=articles,
                            users=users)

    except Exception as e:
        app.logger.error(f"Search error: {str(e)}")
        flash("An error occurred during search", "error")
        return redirect(url_for('index'))

@app.route('/api/search')
@login_required
@approved_required
def api_search():
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify({'articles': [], 'users': []})
    
    conn = get_conn()
    if not conn:
        return jsonify({'error': 'Database unavailable'}), 500
        
    try:
        cursor = conn.cursor()
        
        # More efficient search with FTS if available
        # Search articles - using prefix search for better performance
        cursor.execute('''
            SELECT title FROM articles
            WHERE LOWER(title) LIKE ? || '%'
            LIMIT 50
        ''', (query,))
        articles = [dict(row) for row in cursor.fetchall()]
        
        # Search users - using prefix search
        cursor.execute('''
            SELECT username, is_bot, is_blocked FROM users
            WHERE LOWER(username) LIKE ? || '%'
            LIMIT 50
        ''', (query,))
        users = [dict(row) for row in cursor.fetchall()]
        
        return jsonify({
            'articles': articles,
            'users': users
        })
    except Exception as e:
        app.logger.error(f"API search error: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/ai-classifier', methods=['GET', 'POST'])
@login_required
@approved_required
@admin_required
def classifier():
    username = request.args.get('username', '') \
        if request.method == 'GET' else request.form.get('username', '').strip()

    if request.method == 'POST':
        action = request.form.get('action')
        revisions = get_user_revisions_diff(username)
        prompt = build_prompt_from_revisions(username, revisions)

        analysis = None
        if action == "analyze":
            analysis = analyze_with_gpt(prompt)
        
            classification_update(username, analysis)
        
        return render_template('prompt.html', 
                               username=username, 
                               revisions=revisions, 
                               prompt=prompt,
                               analysis=analysis)
    
    if username:
        revisions = get_user_revisions_diff(username)
        prompt = build_prompt_from_revisions(username, revisions)
        analysis = analyze_with_gpt(prompt)

        classification_update(username, analysis)

        return render_template('prompt.html',
                             username=username,
                             revisions=revisions,
                             prompt=prompt,
                             analysis=analysis)
    
    return render_template('prompt.html')

if __name__ == '__main__':
    init_db(DB_PATH)
    try:
        first_admin()
    except Exception as e:
        print(f"Error creating admin user: {e}")
    app.run(debug=True)