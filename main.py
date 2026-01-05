import os
import uuid
from typing import Optional
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from supabase import create_client, Client, ClientOptions
from github import Github
from gotrue import SyncSupportedStorage
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.secret_key = os.getenv("GMAIL_APP_PASSWORD", "nova_secret_key_123")

# CONFIGURATION
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip('/')
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = "TTLpro9129/Nova"

class FlaskSessionStorage(SyncSupportedStorage):
    def __init__(self): self.storage = session
    def get_item(self, key: str) -> Optional[str]: return self.storage.get(key)
    def set_item(self, key: str, value: str) -> None: self.storage[key] = value
    def remove_item(self, key: str) -> None: self.storage.pop(key, None)

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(storage=FlaskSessionStorage()))
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    print("✅ NOVA HYBRIDE CONNECTÉ (GITHUB + SUPABASE)")
except Exception as e:
    print(f"❌ ERREUR INIT : {e}")

class UserCtx(dict): __getattr__ = dict.get

def get_user_context():
    try:
        res = supabase.auth.get_user()
        if res and res.user:
            p = supabase.table("profiles").select("*").eq("id", res.user.id).single().execute()
            return UserCtx({"id": res.user.id, "username": p.data['username'], "is_admin": p.data.get('is_admin', False)})
    except: pass
    return None

@app.route('/')
def index():
    user = get_user_context()
    items = supabase.table("apps").select("*").order("created_at", desc=True).execute().data
    users_list = []
    if user and user.is_admin:
        users_list = supabase.table("profiles").select("*").execute().data
    if user:
        for i in items: i['can_delete'] = (user.is_admin or str(i.get('owner_id')) == str(user.id))
    return render_template('index.html', user=user, items=items, users=users_list)

@app.route('/upload', methods=['POST'])
def upload():
    user = get_user_context()
    file = request.files.get('file')
    if not user or not file: return jsonify({"error": "Auth/File missing"}), 400
    clean_filename = secure_filename(file.filename)
    display_name = file.filename.rsplit('.', 1)[0]
    ext = clean_filename.split('.')[-1].lower()
    temp_path = os.path.join("/tmp", clean_filename)
    if not os.path.exists("/tmp"): os.makedirs("/tmp")
    file.save(temp_path)
    try:
        tag = f"v-{uuid.uuid4().hex[:8]}"
        release = repo.create_git_release(tag=tag, name=display_name, message=f"By {user.username}")
        asset = release.upload_asset(path=temp_path, label=clean_filename, content_type='application/octet-stream')
        cfg = {"exe": ("PC", "fa-brands fa-windows", "text-cyan-400"), "apk": ("ANDROID", "fa-brands fa-android", "text-green-400"), "zip": ("ZIP", "fa-solid fa-file-zipper", "text-yellow-500")}
        label, icon, color = cfg.get(ext, ("FILE", "fa-solid fa-box", "text-blue-400"))
        supabase.table("apps").upsert({
            "name": display_name, "owner": user.username, "owner_id": user.id, 
            "file": clean_filename, "storage_path": asset.browser_download_url,
            "type": label, "color": color, "icon_class": icon
        }, on_conflict="file").execute()
        os.remove(temp_path)
        return jsonify({"success": True})
    except Exception as e:
        if os.path.exists(temp_path): os.remove(temp_path)
        return jsonify({"error": str(e)}), 500

@app.route('/change_username', methods=['POST'])
def change_username():
    user = get_user_context()
    new_u = request.form.get('new_username')
    if user and new_u:
        supabase.table("profiles").update({"username": new_u}).eq("id", user.id).execute()
    return redirect('/')

@app.route('/update_icon/<path:filename>', methods=['POST'])
def update_icon(filename):
    user = get_user_context()
    image = request.files.get('image')
    if not user or not image: return jsonify({"error": "Missing image"}), 400
    try:
        ext = image.filename.split('.')[-1].lower()
        path = f"logos/{user.id}/{filename}.{ext}"
        image_data = image.read()
        supabase.storage.from_("files").upload(path, image_data, {"upsert": "true", "content-type": f"image/{ext}"})
        icon_url = f"{SUPABASE_URL}/storage/v1/object/public/files/{path}"
        supabase.table("apps").update({"preview_icon": icon_url}).eq("file", filename).execute()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/download/<path:filename>')
def download(filename):
    res = supabase.table("apps").select("storage_path").eq("file", filename).single().execute()
    return redirect(res.data['storage_path']) if res.data else ("404", 404)

@app.route('/login', methods=['POST'])
def login():
    u, p = request.form.get('username'), request.form.get('password')
    try: supabase.auth.sign_in_with_password({"email": f"{u}@hub.com", "password": p})
    except: flash("Login error")
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
    if get_user_context(): supabase.table("apps").delete().eq("file", filename).execute()
    return redirect('/')

@app.route('/admin/delete_user', methods=['POST'])
def admin_delete():
    user = get_user_context()
    if user and user.is_admin:
        supabase.table("profiles").delete().eq("username", request.form.get('target')).execute()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, port=8000)