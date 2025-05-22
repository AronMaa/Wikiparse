from flask import Flask, render_template, request, url_for, redirect, flash
import sqlite3
from datetime import datetime
from populate import init_db, fetch_revisions_from_api, update_database
from queries import fetch_users, fetch_revisions_db

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
    """Article revision history"""
    try:
        conn = get_conn()
        revisions = fetch_revisions_db(conn, article_title=title, limit=100)
        return render_template('article_revisions.html', 
                             title=title, 
                             revisions=revisions)
    except Exception as e:
        flash(f"Error loading revisions: {str(e)}", "error")
        return render_template('article_revisions.html', title=title, revisions=[])
    finally:
        if conn: conn.close()

@app.route('/users')
def users_list():
    """Filterable user list"""
    try:
        conn = get_conn()
        users = fetch_users(
            conn,
            article_title=request.args.get('article'),
            bots_only=request.args.get('bots') == '1',
            ips_only=request.args.get('ips') == '1',
            blocked_only=request.args.get('blocked') == '1',
            active_within_days=request.args.get('active_days', type=int)
        )
        return render_template('users.html', users=users, filters=request.args)
    except Exception as e:
        flash(f"Error loading users: {str(e)}", "error")
        return render_template('users.html', users=[], filters=request.args)
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

if __name__ == '__main__':
    init_db(DB_PATH)
    app.run(debug=True)