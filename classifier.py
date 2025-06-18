from flask import Flask, render_template, request
import requests
import difflib
from openai import OpenAI

app = Flask(__name__)

# Configurez votre clé API
client = OpenAI(
  api_key="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"
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
    """Construit un prompt formaté à partir de révisions"""
    prompt = (
        f"Analyse des contributions de l'utilisateur Wikipédia '{username}':\n\n"
        "Pour chaque modification, examiner la nature des changements, le commentaire de modification et les flags.\n\n"
    )
    
    for rev in revisions:
        prompt += (
            f"{rev['title']}\n"
            f"Flags: {rev.get('flags', 'Aucun')}\n"
            f"Commentaire: {rev.get('comment', 'Aucun')}\n"
            f"Modifications:\n{rev.get('diff', '[Diff non disponible]')}"[:1000]+"\n\n"
        )

    prompt += (
        "Classifiez l'utilisateur comme 'pro-palestine', 'pro-israel' ou 'neutre' avec une courte justification en une vingtaine de mots."
    )
    return prompt

def analyze_with_gpt(prompt):
    """Analyse le prompt avec l'API OpenAI"""
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Vous êtes un analyste expert des contributions Wikipédia."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Erreur d'analyse: {str(e)}"