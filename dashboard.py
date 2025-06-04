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

def show_article_stats(conn, article_title):
    """Affiche les statistiques pour un article spécifique"""
    stats = {}
    
    # Requêtes pour les statistiques de l'article
    article_stats_query = """
        SELECT 
            COUNT(r.id) as revisions,
            COUNT(DISTINCT r.user_id) as unique_users,
            MAX(r.timestamp) as last_updated
        FROM articles a
        LEFT JOIN revisions r ON a.id = r.article_id
        WHERE a.title = ?
        GROUP BY a.id
    """
    article_stats = conn.execute(article_stats_query, (article_title,)).fetchone()
    
    if article_stats:
        stats['revisions'] = article_stats['revisions']
        stats['unique_users'] = article_stats['unique_users']
        stats['last_updated'] = datetime.fromisoformat(article_stats['last_updated']).strftime('%d-%m-%Y %H:%M') if article_stats['last_updated'] else "N/A"
        
        # Pourcentages de bots et comptes bloqués
        user_stats_query = """
            SELECT 
                ROUND(100.0 * SUM(u.is_bot) / COUNT(DISTINCT r.user_id), 1) as bots_percentage,
                ROUND(100.0 * SUM(u.is_blocked) / COUNT(DISTINCT r.user_id), 1) as blocked_percentage
            FROM revisions r
            JOIN users u ON r.user_id = u.id
            JOIN articles a ON r.article_id = a.id
            WHERE a.title = ?
        """
        user_stats = conn.execute(user_stats_query, (article_title,)).fetchone()
        stats['bots_percentage'] = user_stats['bots_percentage'] if user_stats and user_stats['bots_percentage'] is not None else 0
        stats['blocked_percentage'] = user_stats['blocked_percentage'] if user_stats and user_stats['blocked_percentage'] is not None else 0
        
        # Utilisateur le plus actif
        top_editor_query = """
            SELECT u.username, COUNT(r.id) as revision_count
            FROM users u
            JOIN revisions r ON u.id = r.user_id
            JOIN articles a ON r.article_id = a.id
            WHERE a.title = ?
            GROUP BY u.id
            ORDER BY revision_count DESC
            LIMIT 1
        """
        top_editor = conn.execute(top_editor_query, (article_title,)).fetchone()
        stats['top_editor'] = f"{top_editor['username']} ({top_editor['revision_count']} révisions)" if top_editor else "N/A"
    
    return stats

def get_tag_statistics(conn, article_titles=None):
    """Analyse la répartition des tags parmi les révisions en utilisant le schéma réel"""
    try:
        # Requête pour récupérer les tags
        base_query = """
            SELECT r.tags as tags_str
            FROM revisions r
            {article_join}
            WHERE r.tags IS NOT NULL AND r.tags != ''
            {article_filter}
        """
        
        # Filtre par article si spécifié
        article_join = ""
        article_filter = ""
        params = []
        if article_titles:
            article_join = "JOIN articles a ON r.article_id = a.id"
            article_filter = "AND a.title IN ({})".format(','.join(['?']*len(article_titles)))
            params = article_titles

        # Exécution de la requête
        revisions = conn.execute(
            base_query.format(article_join=article_join, article_filter=article_filter),
            params
        ).fetchall()

        # Comptage des tags
        tag_counts = {}
        total_tags = 0
        
        for rev in revisions:
            tags = rev['tags_str'].split(',')
            for tag in tags:
                tag = tag.strip()
                if tag:  # Ignorer les tags vides
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                    total_tags += 1

        # Création du DataFrame
        if tag_counts:
            tags_df = pd.DataFrame({
                'tag_name': list(tag_counts.keys()),
                'count': list(tag_counts.values()),
                'percentage': [round(100 * count / total_tags, 1) if total_tags > 0 else 0 
                              for count in tag_counts.values()]
            })
            return tags_df.sort_values('count', ascending=False)
        else:
            return pd.DataFrame(columns=['tag_name', 'count', 'percentage'])

    except Exception as e:
        st.error(f"Erreur dans get_tag_statistics: {str(e)}")
        return pd.DataFrame(columns=['tag_name', 'count', 'percentage'])

def show_admin_dashboard():
    st.set_page_config(layout="wide")
    st.title("Tableau de Bord d'Analyse des Articles Wikipédia")

    conn = get_db_connection()

    # --- Sidebar Filters ---
    st.sidebar.header("Filtres Globaux")
    
    # Récupérer tous les articles
    all_articles = pd.read_sql("SELECT title FROM articles ORDER BY title", conn)['title'].tolist()
    
    # Sélection d'articles avec possibilité de saisie manuelle
    selected_articles = st.sidebar.multiselect(
        "Choisir des articles à analyser:",
        options=all_articles,
        default=[]
    )

    # --- Aperçu Général ---
    st.header("Aperçu Général")
    
    # Centrer les métriques
    col1, col2, col3 = st.columns(3)
    
    with col1:
        total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        st.metric("Nombre Total d'Articles", total_articles)
    
    with col2:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        st.metric("Nombre Total d'Utilisateurs", total_users)
    
    with col3:
        total_revisions = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        st.metric("Nombre Total de Révisions", total_revisions)

    l2col1, l2col2, l2col3 = st.columns(3)

    top_editor_query = """
        SELECT u.username, COUNT(r.id) as revision_count
        FROM users u
        JOIN revisions r ON u.id = r.user_id
        GROUP BY u.id
        ORDER BY revision_count DESC
        LIMIT 1
    """
    top_editor = conn.execute(top_editor_query).fetchone()
    if top_editor:
        l2col1.metric(
            "Utilisateur le Plus Actif",
            f"{top_editor['username']}",
            f"({top_editor['revision_count']} révisions)"
        )
    else:
        l2col1.metric("Utilisateur le Plus Actif", "N/A")

    most_controversial_query = """
        SELECT a.title, COUNT(r.id) as revision_count
        FROM articles a
        JOIN revisions r ON a.id = r.article_id
        GROUP BY a.id
        ORDER BY revision_count DESC
        LIMIT 1
    """
    most_controversial_article = conn.execute(most_controversial_query).fetchone()
    if most_controversial_article:
        l2col2.metric(
            "Article le Plus Controversé",
            f"{most_controversial_article['title']}",
            f"({most_controversial_article['revision_count']} révisions)"
        )
    else:
        l2col2.metric("Article le Plus Controversé", "N/A")

    last_revised_query = """
        SELECT a.title, r.timestamp
        FROM revisions r
        JOIN articles a ON r.article_id = a.id
        ORDER BY r.timestamp DESC
        LIMIT 1
    """
    last_revised_article = conn.execute(last_revised_query).fetchone()
    if last_revised_article:
        last_revision_date = datetime.fromisoformat(last_revised_article['timestamp']).strftime('%d-%m-%Y %H:%M')
        l2col3.metric(
            "Dernier Article Révisé",
            f"{last_revised_article['title']}",
            f"({last_revision_date})",
            delta_color ='off'
        )
    else:
        l2col3.metric("Dernier Article Révisé", "N/A")

    # --- Statistiques par Article ---
    if selected_articles:
        st.header("Statistiques par Article")
        
        # Afficher les stats pour chaque article sélectionné
        for article in selected_articles:
            with st.expander(f"Statistiques pour: {article}"):
                stats = show_article_stats(conn, article)
                
                if stats:
                    cols = st.columns(4)
                    cols[0].metric("Nombre de Révisions", stats['revisions'])
                    cols[1].metric("Nombre d'Utilisateurs Uniques", stats['unique_users'])
                    cols[2].metric("Pourcentage de Bots", f"{stats['bots_percentage']}%")
                    cols[3].metric("Pourcentage de Comptes Bloqués", f"{stats['blocked_percentage']}%")
                    
                    cols2 = st.columns(2)
                    cols2[0].metric("Dernière Révision", stats['last_updated'])
                    cols2[1].metric("Utilisateur le Plus Actif", stats['top_editor'])
                else:
                    st.warning(f"Aucune donnée disponible pour l'article: {article}")

    # --- Onglets Principaux ---
    tab_articles, tab_revisions, tab_users = st.tabs(["Articles", "Révisions", "Utilisateurs"])

    with tab_articles:
        st.header("Analyse des Articles")
        
        # Filtres spécifiques aux articles
        min_revisions_col, num_input_col = st.columns([3, 1])
        with min_revisions_col:
            min_revisions = st.slider(
                "Minimum de révisions",
                min_value=0,
                max_value=1000,
                value=10,
                disabled=bool(selected_articles))
        with num_input_col:
            if not selected_articles:
                custom_min = st.number_input(
                    "Entrez une valeur exacte",
                    min_value=0,
                    max_value=10000,
                    value=10,
                    step=1,
                    key="custom_min_rev")
                min_revisions = custom_min if custom_min != 10 else min_revisions
        
        # Requête des articles
        if selected_articles:
            articles_query = """
                SELECT a.id, a.title,
                       COUNT(r.id) AS revisions,
                       COUNT(DISTINCT r.user_id) AS unique_users,
                       MAX(r.timestamp) AS last_updated
                FROM articles a
                LEFT JOIN revisions r ON a.id = r.article_id
                WHERE a.title IN ({})
                GROUP BY a.id
                ORDER BY revisions DESC
            """.format(','.join(['?']*len(selected_articles)))
            articles_df = pd.read_sql(articles_query, conn, params=selected_articles)
        else:
            articles_query = """
                SELECT a.id, a.title,
                       COUNT(r.id) AS revisions,
                       COUNT(DISTINCT r.user_id) AS unique_users,
                       MAX(r.timestamp) AS last_updated
                FROM articles a
                LEFT JOIN revisions r ON a.id = r.article_id
                GROUP BY a.id
                HAVING revisions >= ?
                ORDER BY revisions DESC
                LIMIT 15
            """
            articles_df = pd.read_sql(articles_query, conn, params=(min_revisions,))
        
        if not articles_df.empty:
            fig = px.bar(
                articles_df,
                x='title',
                y='revisions',
                title='Articles par Nombre de Révisions',
                labels={'title': 'Article', 'revisions': 'Nombre de Révisions'},
                color='title' if selected_articles else None
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Aucun article ne correspond aux critères sélectionnés.")

    with tab_revisions:
        st.header("Analyse des Révisions")
        
        if selected_articles:
            # Chronologie pour les articles sélectionnés
            timeline_query = """
                SELECT a.title, date(r.timestamp) as day, COUNT(*) as revisions
                FROM revisions r
                JOIN articles a ON r.article_id = a.id
                WHERE a.title IN ({})
                GROUP BY a.title, day
                ORDER BY day
            """.format(','.join(['?']*len(selected_articles)))
            timeline_df = pd.read_sql(timeline_query, conn, params=selected_articles)
            
            if not timeline_df.empty:
                fig = px.line(
                    timeline_df,
                    x='day',
                    y='revisions',
                    color='title',
                    title=f"Activité de Révision pour les Articles Sélectionnés",
                    labels={'day': 'Date', 'revisions': 'Révisions par Jour'}
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Aucune donnée de révision pour les articles sélectionnés.")
        else:
            # Chronologie globale
            timeline_df = pd.read_sql("""
                SELECT date(timestamp) as day, COUNT(*) as revisions
                FROM revisions
                GROUP BY day
                ORDER BY day
            """, conn)
            
            if not timeline_df.empty:
                fig = px.line(
                    timeline_df,
                    x='day',
                    y='revisions',
                    title="Activité de Révision Globale",
                    labels={'day': 'Date', 'revisions': 'Révisions par Jour'}
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Aucune donnée de révision disponible.")
        
            st.subheader("Répartition des Tags")
            tag_stats = get_tag_statistics(conn, selected_articles if selected_articles else None)

            if not tag_stats.empty:
                col1, col2 = st.columns(2)
                with col1:
                    fig = px.pie(
                        tag_stats,
                        names='tag_name',
                        values='count',
                        title='Répartition des Tags',
                        hover_data=['percentage']
                    )
                    st.plotly_chart(fig, use_container_width=True)
                with col2:
                    st.dataframe(tag_stats)
            else:
                st.warning("Aucune donnée de tag disponible.")
        
    with tab_users:
        st.header("Analyse des Utilisateurs")
        
        # Filtres utilisateurs
        num_users_col, custom_num_col = st.columns([3, 1])
        with num_users_col:
            num_users = st.slider("Nombre d'utilisateurs à afficher", 5, 100, 20)
        with custom_num_col:
            custom_num = st.number_input(
                "Entrez un nombre exact",
                min_value=1,
                max_value=500,
                value=20,
                step=1,
                key="custom_num_users")
            num_users = custom_num if custom_num != 20 else num_users
        show_bots = st.checkbox("Inclure les bots", value=True)
        
        # Requête utilisateurs
        user_query = """
            SELECT u.username,
                   COUNT(r.id) as revisions,
                   u.is_bot,
                   u.is_ip,
                   u.is_blocked
            FROM users u
            LEFT JOIN revisions r ON u.id = r.user_id
            {article_filter}
            GROUP BY u.id
            HAVING revisions > 0
            {bot_filter}
            ORDER BY revisions DESC
            LIMIT ?
        """
        
        params = [num_users]
        article_filter = ""
        bot_filter = ""
        
        if selected_articles:
            article_filter = "JOIN articles a ON r.article_id = a.id WHERE a.title IN ({})".format(','.join(['?']*len(selected_articles)))
            params = selected_articles + params
        
        if not show_bots:
            bot_filter = "AND u.is_bot = 0"
        
        user_query = user_query.format(article_filter=article_filter, bot_filter=bot_filter)
        user_activity_df = pd.read_sql(user_query, conn, params=params)
        
        if not user_activity_df.empty:
            user_activity_df['Type Utilisateur'] = user_activity_df.apply(
                lambda x: 'Bot' if x['is_bot'] == 1 else ('IP' if x['is_ip'] == 1 else 'Humain'), 
                axis=1
            )
            
            fig = px.scatter(
                user_activity_df,
                x='username',
                y='revisions',
                color='Type Utilisateur',
                size='revisions',
                hover_name='username',
                title=f"Activité des Utilisateurs ({'Tous les articles' if not selected_articles else ', '.join(selected_articles)})",
                labels={'revisions': 'Nombre de Révisions'},
                color_discrete_map={'Bot': 'red', 'IP': 'orange', 'Humain': 'blue'}
            )
            fig.update_layout(xaxis_title='', xaxis_showticklabels=False)
            st.plotly_chart(fig, use_container_width=True)
            
            # Analyse des types d'utilisateurs
            st.subheader("Répartition des Types d'Utilisateurs")
            
            if selected_articles:
                user_types_query = """
                    SELECT
                        SUM(CASE WHEN u.is_bot = 1 THEN 1 ELSE 0 END) as "Bots",
                        SUM(CASE WHEN u.is_ip = 1 THEN 1 ELSE 0 END) as "Adresses IP",
                        SUM(CASE WHEN u.is_blocked = 1 THEN 1 ELSE 0 END) as "Bloqués",
                        SUM(CASE WHEN u.is_bot = 0 AND u.is_ip = 0 THEN 1 ELSE 0 END) as "Normaux"
                    FROM (
                        SELECT DISTINCT u.id, u.is_bot, u.is_ip, u.is_blocked
                        FROM users u
                        JOIN revisions r ON u.id = r.user_id
                        JOIN articles a ON r.article_id = a.id
                        WHERE a.title IN ({})
                    ) u
                """.format(','.join(['?']*len(selected_articles)))
                user_types_df = pd.read_sql(user_types_query, conn, params=selected_articles)
            else:
                user_types_df = pd.read_sql("""
                    SELECT
                        SUM(is_bot) as "Bots",
                        SUM(is_ip) as "Adresses IP",
                        SUM(is_blocked) as "Bloqués",
                        SUM(CASE WHEN is_bot = 0 AND is_ip = 0 THEN 1 ELSE 0 END) as "Normaux"
                    FROM users
                """, conn)
            
            if not user_types_df.empty:
                col1, col2 = st.columns(2)
                with col1:
                    fig = px.pie(
                        user_types_df.melt(var_name='Type', value_name='Count'),
                        names='Type',
                        values='Count',
                        title='Répartition des Utilisateurs'
                    )
                    st.plotly_chart(fig, use_container_width=True)
                with col2:
                    st.dataframe(user_types_df)
        else:
            st.warning("Aucune donnée utilisateur disponible.")

    conn.close()

if __name__ == "__main__":
    show_admin_dashboard()