import os
import uuid
from typing import Optional
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from supabase import create_client, Client, ClientOptions
from github import Github
from gotrue import SyncSupportedStorage
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# Charge les variables du fichier .env
load_dotenv()

app = Flask(__name__)
# Autorise les fichiers jusqu'à 500 Mo via Flask
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

# --- RÉCUPÉRATION DES CONFIGURATIONS ---
# On utilise GMAIL_APP_PASSWORD comme clé secrète Flask pour les sessions
app.secret_key = os.getenv("GMAIL_APP_PASSWORD", "nova_default_secret_key")

# Configuration Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://grbhemxpqrqialezsywj.supabase.co/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Configuration GitHub (Pour le stockage illimité)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") 
REPO_NAME = "TTLpro9129/Nova"

# --- INITIALISATION ---
class FlaskSessionStorage(SyncSupportedStorage):
    def __init__(self): self.storage = session
    def get_item(self, key: str) -> Optional[str]: return self.storage.get(key)
    def set_item(self, key: str, value: str) -> None: self.storage[key] = value
    def remove_item(self, key: str) -> None: self.storage.pop(key, None)

try:
    # Client Supabase pour la base de données
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(storage=FlaskSessionStorage()))
    
    # Client GitHub pour le stockage des fichiers
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    print("✅ HUB NOVA CONNECTÉ : SUPABASE (DB) + GITHUB (STORAGE)")
except Exception as e: 
    print(f"❌ ERREUR INITIALISATION : {e}")

class UserCtx(dict): __getattr__ = dict.get

def get_user_context():
    try:
        res = supabase.auth.get_user()
        if res and res.user:
            p = supabase.table("profiles").select("*").eq("id", res.user.id).single().execute()
            return UserCtx({"id": res.user.id, "username": p.data['username'], "is_admin": p.data.get('is_admin', False)})
    except: pass
    return None

# --- ROUTES PRINCIPALES ---

@app.route('/')
def index():
    user = get_user_context()
    items, users_list = [], []
    try:
        items = supabase.table("apps").select("*").order("created_at", desc=True).execute().data
        if user:
            for i in items: i['can_delete'] = (user.is_admin or str(i.get('owner_id')) == str(user.id))
            if user.is_admin:
                users_list = supabase.table("profiles").select("*").execute().data
    except: pass
    return render_template('index.html', user=user, items=items, users=users_list)

@app.route('/upload', methods=['POST'])
def upload():
    user = get_user_context()
    file = request.files.get('file')
    if not user or not file: return jsonify({"error": "Connexion requise ou fichier manquant"}), 400
    
    clean_filename = secure_filename(file.filename)
    display_name = file.filename.rsplit('.', 1)[0]
    ext = clean_filename.split('.')[-1].lower()
    
    # 1. SAUVEGARDE TEMPORAIRE (Indispensable pour Render)
    temp_path = os.path.join("/tmp", clean_filename)
    if not os.path.exists("/tmp"): os.makedirs("/tmp")
    file.save(temp_path)
    
    try:
        # 2. ENVOI SUR GITHUB (RELEASE ASSET)
        # On crée une release unique pour chaque fichier pour ne pas avoir de limite
        release_tag = f"v-{uuid.uuid4().hex[:8]}"
        release = repo.create_git_release(
            tag=release_tag, 
            name=f"Release: {display_name}", 
            message=f"Posté par {user.username}",
            draft=False, prerelease=False
        )
        
        # Upload du fichier physique sur GitHub
        asset = release.upload_asset(path=temp_path, label=clean_filename, content_type='application/octet-stream')
        github_url = asset.browser_download_url # C'est le lien direct de téléchargement

        # 3. ENREGISTREMENT DANS SUPABASE (On stocke juste l'URL GitHub)
        cfg = {
            "exe": ("PC", "fa-brands fa-windows", "text-cyan-400"),
            "apk": ("ANDROID", "fa-brands fa-android", "text-green-400"),
            "ipa": ("iOS", "fa-brands fa-apple", "text-slate-300"),
            "zip": ("ARCHIVE", "fa-solid fa-file-zipper", "text-yellow-500"),
            "rar": ("ARCHIVE", "fa-solid fa-file-zipper", "text-yellow-600"),
            "7z": ("ARCHIVE", "fa-solid fa-file-zipper", "text-orange-500")
        }
        label, icon, color = cfg.get(ext, (ext.upper(), "fa-solid fa-box-open", "text-blue-400"))
        
        supabase.table("apps").upsert({
            "name": display_name, 
            "owner": user.username, 
            "owner_id": user.id, 
            "file": clean_filename, 
            "storage_path": github_url, # URL de GitHub stockée en base
            "type": label, 
            "color": color, 
            "icon_class": icon, 
            "version": "1.0.0"
        }, on_conflict="file").execute()

        os.remove(temp_path) # Supprime le fichier du serveur Render après l'envoi
        return jsonify({"success": True})
    except Exception as e:
        if os.path.exists(temp_path): os.remove(temp_path)
        print(f"Erreur Upload GitHub: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/download/<path:filename>')
def download(filename):
    try:
        # Récupère l'URL GitHub depuis Supabase et redirige l'utilisateur
        res = supabase.table("apps").select("storage_path").eq("file", filename).single().execute()
        if res.data and res.data['storage_path']:
            return redirect(res.data['storage_path'])
        return "Fichier non trouvé", 404
    except: return "Erreur 404", 404

@app.route('/update_icon/<path:filename>', methods=['POST'])
def update_icon(filename):
    user = get_user_context()
    image = request.files.get('image')
    if not user or not image: return jsonify({"error": "Échec"}), 400
    try:
        # Les icônes sont petites, on peut les laisser sur Supabase Storage
        ext = image.filename.split('.')[-1]
        path = f"logos/{user.id}/{filename}.{ext}"
        supabase.storage.from_("files").upload(path, image.read(), {"upsert": "true"})
        icon_url = f"{SUPABASE_URL}storage/v1/object/public/files/{path}"
        supabase.table("apps").update({"preview_icon": icon_url}).eq("file", filename).execute()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

# --- ROUTES D'AUTHENTIFICATION ---

@app.route('/login', methods=['POST'])
def login():
    u, p = request.form.get('username'), request.form.get('password')
    try: supabase.auth.sign_in_with_password({"email": f"{u}@hub.com", "password": p})
    except: flash("Erreur de connexion")
    return redirect('/')

@app.route('/register', methods=['POST'])
def register():
    u, p = request.form.get('username'), request.form.get('password')
    try:
        res = supabase.auth.sign_up({"email": f"{u}@hub.com", "password": p})
        if res.user: supabase.table("profiles").insert({"id": res.user.id, "username": u}).execute()
    except Exception as e: flash(str(e))
    return redirect('/')

@app.route('/logout')
def logout():
    supabase.auth.sign_out(); session.clear()
    return redirect('/')

@app.route('/delete/<path:filename>', methods=['POST'])
def delete_item(filename):
    user = get_user_context()
    if user:
        # Suppression de l'entrée dans la base Supabase
        supabase.table("apps").delete().eq("file", filename).execute()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, port=8000)