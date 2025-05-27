import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
from datetime import datetime

DB_PATH = "wikipedia.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def show_admin_dashboard():
    st.set_page_config(layout="wide")
    st.title("Wikipedia Articles Analytics Dashboard")
    
    conn = get_db_connection()
    
    # Overview Metrics
    st.header("Overview Metrics")
    col1, col2, col3, col4 = st.columns(4)
    
    total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_revisions = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    bots_percentage = conn.execute("""
        SELECT ROUND(100.0 * SUM(is_bot) / COUNT(*), 1) 
        FROM users
    """).fetchone()[0]
    
    col1.metric("Total Articles", total_articles)
    col2.metric("Total Users", total_users)
    col3.metric("Total Revisions", total_revisions)
    col4.metric("Bot Percentage", f"{bots_percentage}%")
    
    # Articles Analysis
    st.header("Articles Analysis")
    
    # Get article data
    articles_df = pd.read_sql("""
        SELECT a.id, a.title, 
               COUNT(r.id) AS revisions,
               COUNT(DISTINCT r.user_id) AS unique_users,
               MAX(r.timestamp) AS last_updated
        FROM articles a
        LEFT JOIN revisions r ON a.id = r.article_id
        GROUP BY a.id
        ORDER BY revisions DESC
    """, conn)
    
    # Filters
    col1, col2 = st.columns(2)
    min_revisions = col1.slider(
        "Minimum revisions", 
        min_value=0, 
        max_value=int(articles_df['revisions'].max()), 
        value=10
    )
    show_top = col2.slider(
        "Show top N articles", 
        min_value=5, 
        max_value=50, 
        value=15
    )
    
    filtered_articles = articles_df[articles_df['revisions'] >= min_revisions].head(show_top)
    
    # Charts
    tab1, tab2, tab3 = st.tabs(["Revisions Distribution", "Activity Timeline", "User Engagement"])
    
    with tab1:
        fig = px.bar(
            filtered_articles,
            x='title',
            y='revisions',
            title='Top Articles by Number of Revisions',
            labels={'title': 'Article', 'revisions': 'Number of Revisions'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        timeline_df = pd.read_sql("""
            SELECT date(timestamp) as day, COUNT(*) as revisions
            FROM revisions
            GROUP BY day
            ORDER BY day
        """, conn)
        
        fig = px.line(
            timeline_df,
            x='day',
            y='revisions',
            title='Revision Activity Over Time',
            labels={'day': 'Date', 'revisions': 'Revisions per Day'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        user_activity_df = pd.read_sql("""
            SELECT u.username, 
                   COUNT(r.id) as revisions,
                   u.is_bot,
                   u.is_ip,
                   u.is_blocked
            FROM users u
            LEFT JOIN revisions r ON u.id = r.user_id
            GROUP BY u.id
            HAVING revisions > 0
            ORDER BY revisions DESC
        """, conn)
        
        fig = px.scatter(
            user_activity_df,
            x='username',
            y='revisions',
            color='is_bot',
            size='revisions',
            title='User Activity (Size = Number of Revisions)',
            labels={'username': 'User', 'revisions': 'Number of Revisions'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # User Type Analysis
    st.header("User Type Analysis")
    
    user_types = pd.read_sql("""
        SELECT 
            SUM(is_bot) as bots,
            SUM(is_ip) as ip_users,
            SUM(is_blocked) as blocked,
            COUNT(*) - SUM(is_bot) - SUM(is_ip) as regular_users
        FROM users
    """, conn)
    
    user_types_melted = user_types.melt().rename(
        columns={'variable': 'user_type', 'value': 'count'}
    )
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig = px.pie(
            user_types_melted,
            names='user_type',
            values='count',
            title='User Type Distribution'
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.dataframe(user_types_melted)
    
    conn.close()

if __name__ == "__main__":
    show_admin_dashboard()