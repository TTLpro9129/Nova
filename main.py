import os
from typing import Optional
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from supabase import create_client, Client, ClientOptions
from gotrue import SyncSupportedStorage
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
# Autorise les fichiers jusqu'à 500 Mo
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.secret_key = os.getenv("GMAIL_APP_PASSWORD", "nova_premium_final_v21")

class UserCtx(dict): __getattr__ = dict.get

class FlaskSessionStorage(SyncSupportedStorage):
    def __init__(self): self.storage = session
    def get_item(self, key: str) -> Optional[str]: return self.storage.get(key)
    def set_item(self, key: str, value: str) -> None: self.storage[key] = value
    def remove_item(self, key: str) -> None: self.storage.pop(key, None)

try:
    url = "https://grbhemxpqrqialezsywj.supabase.co/"
    key = os.getenv("SUPABASE_KEY", "").strip()
    supabase: Client = create_client(url, key, options=ClientOptions(storage=FlaskSessionStorage()))
    print("✅ HUB NOVA CONNECTÉ")
except Exception as e: print(f"❌ ERREUR INIT : {e}")

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
    if not user or not file: return jsonify({"error": "Auth/File missing"}), 400
    
    clean_filename = secure_filename(file.filename)
    display_name = file.filename.rsplit('.', 1)[0]
    ext = clean_filename.split('.')[-1].lower()
    path = f"public/{user.id}/{clean_filename}"
    
    try:
        supabase.storage.from_("files").upload(path, file.read(), {"upsert": "true"})
        
        # Configuration des types incluant ZIP, RAR, 7Z
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
            "name": display_name, "owner": user.username, "owner_id": user.id, 
            "file": clean_filename, "storage_path": path, "type": label, 
            "color": color, "icon_class": icon, "version": "1.0.0"
        }, on_conflict="file").execute()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/update_icon/<path:filename>', methods=['POST'])
def update_icon(filename):
    user = get_user_context()
    image = request.files.get('image')
    if not user or not image: return jsonify({"error": "Failed"}), 400
    try:
        ext = image.filename.split('.')[-1]
        path = f"logos/{user.id}/{filename}.{ext}"
        supabase.storage.from_("files").upload(path, image.read(), {"upsert": "true"})
        icon_url = f"{url}storage/v1/object/public/files/{path}"
        supabase.table("apps").update({"preview_icon": icon_url}).eq("file", filename).execute()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

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

@app.route('/download/<path:filename>')
def download(filename):
    try:
        res = supabase.table("apps").select("storage_path").eq("file", filename).single().execute()
        signed = supabase.storage.from_("files").create_signed_url(res.data['storage_path'], 3600)
        return redirect(signed['signedURL'])
    except: return "404", 404

@app.route('/delete/<path:filename>', methods=['POST'])
def delete_item(filename):
    if get_user_context(): supabase.table("apps").delete().eq("file", filename).execute()
    return redirect('/')

@app.route('/change_username', methods=['POST'])
def change_username():
    user = get_user_context()
    new_u = request.form.get('new_username')
    if user and new_u: supabase.table("profiles").update({"username": new_u}).eq("id", user.id).execute()
    return redirect('/')

@app.route('/admin/delete_user', methods=['POST'])
def admin_delete():
    user = get_user_context()
    if user and user.is_admin: supabase.table("profiles").delete().eq("username", request.form.get('target')).execute()
    return redirect('/')

@app.route('/admin/change_username', methods=['POST'])
def admin_rename():
    user = get_user_context()
    if user and user.is_admin:
        target, new_u = request.form.get('target'), request.form.get('new_username')
        supabase.table("profiles").update({"username": new_u}).eq("username", target).execute()
    return redirect('/')

if __name__ == '__main__': app.run(debug=True, port=8000)