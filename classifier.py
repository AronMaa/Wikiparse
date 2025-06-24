import sqlite3
import requests
import difflib
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

# Configurez votre clé API
client = OpenAI(
  api_key = os.getenv("API_KEY")
)

def get_user_revisions_diff(username, limit=10):
    """Récupère les dernières révisions d'un utilisateur sur Wikipédia (langue FR)"""
    S = requests.Session()
    URL = "https://fr.wikipedia.org/w/api.php"

    PARAMS = {
        "action": "query",
        "list": "usercontribs",
        "ucuser": username,
        "uclimit": limit,
        "ucprop": "title|timestamp|comment|flags|ids|sizediff",
        "format": "json"
    }

    try:
        response = S.get(url=URL, params=PARAMS, timeout=15)
        data = response.json()
        revisions = data.get("query", {}).get("usercontribs", [])
        
        for rev in revisions:
            revid = rev['revid']
            current_content = get_revision_content(revid)
            
            parent_content = ""
            if 'parentid' in rev:
                parent_content = get_revision_content(rev['parentid'])
            
            rev['content'] = current_content
            rev['diff'] = generate_diff(parent_content, current_content) if parent_content else "[Première version]"
            rev['flags'] = ', '.join(rev.get('flags', [])) or 'Aucun'
            
        return revisions
    except Exception as e:
        return [{"title": "[Erreur]", "content": str(e)}]

def get_revision_content(revid):
    """Récupère le contenu d'une révision spécifique"""
    S = requests.Session()
    URL = "https://fr.wikipedia.org/w/api.php"
    
    PARAMS = {
        "action": "query",
        "prop": "revisions",
        "revids": revid,
        "rvprop": "content|ids",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2"
    }
    
    try:
        response = S.get(url=URL, params=PARAMS, timeout=10)
        data = response.json()
        
        if 'pages' in data.get('query', {}):
            for page in data['query']['pages']:
                if 'revisions' in page:
                    return page['revisions'][0]['slots']['main']['content']
        return ""
    except Exception as e:
        print(f"Erreur révision {revid}: {str(e)}")
        return ""

def generate_diff(old_text, new_text):
    """Génère un diff lisible entre deux versions"""
    differ = difflib.Differ()
    diff = list(differ.compare(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True)
    ))
    
    result = []
    for line in diff:
        if line.startswith('+ ') and not line.startswith('+++'):
            result.insert(0,f"<span class='added'>{line[2:]}</span>")
        elif line.startswith('- ') and not line.startswith('---'):
            result.append(f"<span class='removed'>{line[2:]}</span>")
    
    return "".join(result) if result else "[Aucun changement de texte détecté]"

def build_prompt_from_revisions(username, revisions):
    prompt = (
        f"Analyse des contributions de l'utilisateur Wikipédia '{username}':\n\n"
        "Tu dois analyser l’ensemble des révisions ci-dessous pour détecter une orientation idéologique potentielle. "
        "Ignore les fautes de frappe ou les contributions techniques.\n\n"
    )

    for rev in revisions:
        prompt += (
            f"--- Article : {rev['title']} ---\n"
            f"Flags : {rev.get('flags', 'Aucun')}\n"
            f"Commentaire : {rev.get('comment', 'Aucun')}\n"
            f"Modifications (extrait) :\n{rev.get('diff', '[Diff non disponible]')[:1000]}\n\n"
        )

    prompt += (
        "Critères d'analyse :\n"
        "- Pro-palestine : modifications ou commentaires qui accusent Israël (colonisation, apartheid, génocide, etc.), mettent en avant les victimes palestiniennes, ou utilisent des sources pro-palestiniennes.\n"
        "- Pro-israel : modifications qui soutiennent les actions militaires d'Israël, parlent de terrorisme palestinien, défendent Tsahal, ou utilisent des sources pro-israéliennes.\n"
        "- Neutre : modifications équilibrées ou factuelles, présentant les deux points de vue, ou évitant les termes émotionnellement chargés.\n\n"
        "Instructions :\n"
        "Classifie l'utilisateur selon l'orientation globale de ses contributions comme 'pro-palestine', 'pro-israel' ou 'neutre'.\n"
        "Donne une justification en 20 à 30 mots.\n\n"
        "Format de réponse :\n"
        "Classification: [pro-palestine/pro-israel/neutre]\n"
        "Justification: []"
    )
    
    return prompt

def analyze_with_gpt(prompt):
    """Analyse le prompt avec l'API OpenAI"""
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Vous êtes un agent spécialisé dans Wikipédia qui combat l'antisémite."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=150
        )
        full_response = response.choices[0].message.content
        classification = parse_analysis(full_response)
        return classification, full_response
    except Exception as e:
        return f"Erreur d'analyse: {str(e)}"
    
def parse_analysis(text):
    # Parse la réponse structurée de GPT
    classification = "neutre"
    
    if 'Classification:' in text and 'Justification:' in text:
        parts = text.split('Justification:')
        classification_part = parts[0].replace('Classification:', '').strip().lower()
        
        if 'palestine' in classification_part:
            classification = 'pro-palestine'
        elif 'israel' in classification_part:
            classification = 'pro-israel'
    
    return classification

def analyze_top_contributors(limit=100):
    """Analyse les utilisateurs les plus actifs et met à jour leur classification"""
    conn = sqlite3.connect("wikipedia.db")
    cursor = conn.cursor()
    
    # Récupérer les utilisateurs avec le plus de contributions
    cursor.execute("""
        SELECT u.username, COUNT(r.id) as contribution_count 
        FROM users u
        JOIN revisions r ON r.user_id = u.id
        WHERE u.is_bot = 0 
          AND u.is_blocked = 0 
          AND u.is_ip = 0
          AND (u.classification IS NULL OR u.classification = '')
        GROUP BY u.id
        ORDER BY contribution_count DESC
        LIMIT ?
    """, (limit,))
    
    top_users = cursor.fetchall()
    
    results = []
    for user in top_users:
        username = user[0]
        try:
            revisions = get_user_revisions_diff(username)
            prompt = build_prompt_from_revisions(username, revisions)
            analysis = analyze_with_gpt(prompt)
            # Mettre à jour la base de données
            cursor.execute("""
                UPDATE users 
                SET classification = ?
                WHERE username = ?
            """, (analysis[0], username))
            
            results.append((username, analysis[0]))
        except Exception as e:
            print(f"Erreur lors de l'analyse de {username}: {str(e)}")
            continue
    
    conn.commit()
    conn.close()
    return results