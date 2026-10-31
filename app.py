from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, abort, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import date, datetime
from functools import wraps
from pathlib import Path
import secrets, json, zipfile, io, csv, re
from html.parser import HTMLParser
from html import escape
from config import SECRET_KEY, UPLOAD_FOLDER, MAX_CONTENT_LENGTH
from database import get_db, init_db

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
ALLOWED = {"png","jpg","jpeg","gif","webp","mp4","webm","pdf","txt","mp3","wav","ogg"}

@app.context_processor
def inject_theme():
    theme = "light"
    if session.get("user_id"):
        with get_db() as db:
            row = db.execute("SELECT theme FROM user_settings WHERE user_id=?", (session["user_id"],)).fetchone()
            if row: theme = row["theme"]
    return {"theme": theme}

@app.before_request
def setup():
    init_db()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

class SafeDiaryHTML(HTMLParser):
    ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "strike", "p", "br", "div", "h1", "h2", "h3", "ul", "ol", "li", "blockquote", "a"}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED_TAGS: return
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith(("https://", "http://", "mailto:")):
                self.parts.append('<a href="'+escape(href, quote=True)+'" rel="noopener noreferrer">')
            else: self.parts.append("<a>")
        else: self.parts.append(f"<{tag}>")
    def handle_endtag(self, tag):
        if tag in self.ALLOWED_TAGS and tag != "br": self.parts.append(f"</{tag}>")
    def handle_data(self, data): self.parts.append(escape(data))
    def result(self): return "".join(self.parts)

def clean_diary_html(value):
    parser=SafeDiaryHTML(); parser.feed(value or ""); parser.close(); return parser.result()

def current_user():
    if "user_id" not in session: return None
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def owned_entry(db, entry_id, uid, include_deleted=False):
    q = "SELECT * FROM diary_entries WHERE id=? AND user_id=?"
    if not include_deleted: q += " AND deleted=0"
    return db.execute(q, (entry_id, uid)).fetchone()

@app.route("/")
def home():
    return redirect(url_for("dashboard") if session.get("user_id") else url_for("login"))

@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        name=request.form.get("name","").strip(); dob=request.form.get("dob","")
        gender=request.form.get("gender","Prefer not to say"); email=request.form.get("email","").strip().lower()
        pw=request.form.get("password",""); confirm=request.form.get("confirm","")
        try:
            born=date.fromisoformat(dob); today=date.today()
            age=today.year-born.year-((today.month,today.day)<(born.month,born.day))
        except Exception: age=-1
        if not name or len(name)>80: flash("Enter a valid name.","error")
        elif age < 13 or born > date.today(): flash("You must be at least 13 years old and enter a valid date of birth.","error")
        elif gender not in ["Male","Female","Non-binary","Prefer not to say"]: flash("Choose a valid gender.","error")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): flash("Enter a valid email address.","error")
        elif len(pw)<8: flash("Use a password with at least 8 characters.","error")
        elif pw!=confirm: flash("Passwords do not match.","error")
        else:
            try:
                with get_db() as db:
                    cur=db.execute("INSERT INTO users(name,dob,gender,email,password_hash) VALUES(?,?,?,?,?)",
                        (name,dob,gender,email,generate_password_hash(pw)))
                    db.execute("INSERT INTO user_settings(user_id) VALUES(?)",(cur.lastrowid,))
                flash("Account created. Please log in.","success")
                return redirect(url_for("login"))
            except Exception: flash("An account with that email already exists.","error")
    return render_template("auth.html", mode="signup")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); pw=request.form.get("password","")
        with get_db() as db: user=db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if user and check_password_hash(user["password_hash"],pw):
            session.clear(); session["user_id"]=user["id"]
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.","error")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    uid=session["user_id"]; today=date.today().isoformat()
    with get_db() as db:
        user=db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        entries=db.execute("SELECT * FROM diary_entries WHERE user_id=? AND deleted=0 AND archived=0 ORDER BY updated_at DESC LIMIT 5",(uid,)).fetchall()
        stats=db.execute("""SELECT COUNT(*) entries, COALESCE(SUM(LENGTH(content)-LENGTH(REPLACE(content,' ',''))+1),0) words
          FROM diary_entries WHERE user_id=? AND deleted=0""",(uid,)).fetchone()
        memories=db.execute("SELECT COUNT(*) n FROM memories WHERE user_id=?",(uid,)).fetchone()["n"]
        mood=db.execute("SELECT mood FROM moods WHERE user_id=? AND mood_date=?",(uid,today)).fetchone()
        settings=db.execute("SELECT theme FROM user_settings WHERE user_id=?",(uid,)).fetchone()
    return render_template("dashboard.html",user=user,entries=entries,stats=stats,memories=memories,mood=mood["mood"] if mood else None,theme=settings["theme"] if settings else "light")

@app.route("/diary")
@login_required
def diary():
    uid=session["user_id"]; q=request.args.get("q","").strip(); folder=request.args.get("folder","")
    with get_db() as db:
        sql="SELECT * FROM diary_entries WHERE user_id=? AND deleted=0 AND archived=0"; args=[uid]
        if q: sql+=" AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)"; args += [f"%{q}%"]*3
        if folder.isdigit(): sql+=" AND folder_id=?"; args.append(int(folder))
        sql+=" ORDER BY pinned DESC, entry_date DESC, updated_at DESC"
        entries=db.execute(sql,args).fetchall()
        folders=db.execute("SELECT * FROM folders WHERE user_id=? ORDER BY name",(uid,)).fetchall()
    return render_template("diary.html",entries=entries,folders=folders,q=q)

@app.route("/entry/new", methods=["GET","POST"])
@app.route("/entry/<int:entry_id>", methods=["GET","POST"])
@login_required
def editor(entry_id=None):
    uid=session["user_id"]
    with get_db() as db:
        entry=owned_entry(db,entry_id,uid) if entry_id else None
        if entry_id and not entry: abort(404)
        if request.method=="POST":
            title=request.form.get("title","").strip() or "Untitled entry"
            content=clean_diary_html(request.form.get("content",""))
            entry_date=request.form.get("entry_date") or date.today().isoformat()
            category=request.form.get("category","Personal Life")
            mood=request.form.get("mood",""); tags=request.form.get("tags","")
            folder_id=request.form.get("folder_id") or None
            if folder_id and not db.execute("SELECT id FROM folders WHERE id=? AND user_id=?",(folder_id,uid)).fetchone(): folder_id=None
            if entry:
                db.execute("""UPDATE diary_entries SET title=?,content=?,entry_date=?,category=?,mood=?,tags=?,folder_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
                    (title,content,entry_date,category,mood,tags,folder_id,entry_id,uid))
            else:
                db.execute("""INSERT INTO diary_entries(user_id,title,content,entry_date,category,mood,tags,folder_id) VALUES(?,?,?,?,?,?,?,?)""",
                    (uid,title,content,entry_date,category,mood,tags,folder_id))
            flash("Entry saved.","success"); return redirect(url_for("diary"))
        folders=db.execute("SELECT * FROM folders WHERE user_id=?",(uid,)).fetchall()
    return render_template("editor.html",entry=entry,folders=folders,today=date.today().isoformat())

@app.route("/entry/<int:entry_id>/action/<action>",methods=["POST"])
@login_required
def entry_action(entry_id,action):
    uid=session["user_id"]
    allowed={"pin":"pinned","favourite":"favourite","archive":"archived","trash":"deleted"}
    if action not in allowed: abort(404)
    with get_db() as db:
        if not owned_entry(db,entry_id,uid,True): abort(404)
        col=allowed[action]
        db.execute(f"UPDATE diary_entries SET {col}=CASE WHEN {col}=1 THEN 0 ELSE 1 END WHERE id=? AND user_id=?",(entry_id,uid))
    return redirect(request.referrer or url_for("diary"))

@app.route("/trash")
@login_required
def trash():
    with get_db() as db: entries=db.execute("SELECT * FROM diary_entries WHERE user_id=? AND deleted=1 ORDER BY updated_at DESC",(session["user_id"],)).fetchall()
    return render_template("listing.html",title="Recently deleted",entries=entries,mode="trash")

@app.route("/entry/<int:entry_id>/restore",methods=["POST"])
@login_required
def restore_entry(entry_id):
    with get_db() as db: db.execute("UPDATE diary_entries SET deleted=0 WHERE id=? AND user_id=?",(entry_id,session["user_id"]))
    return redirect(url_for("trash"))

@app.route("/entry/<int:entry_id>/delete",methods=["POST"])
@login_required
def delete_entry(entry_id):
    with get_db() as db: db.execute("DELETE FROM diary_entries WHERE id=? AND user_id=? AND deleted=1",(entry_id,session["user_id"]))
    return redirect(url_for("trash"))

@app.route("/folders",methods=["GET","POST"])
@login_required
def folders():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            name=request.form.get("name","").strip()
            if name: db.execute("INSERT INTO folders(user_id,name,icon) VALUES(?,?,?)",(uid,name,request.form.get("icon","📁")))
        items=db.execute("SELECT f.*,COUNT(e.id) count FROM folders f LEFT JOIN diary_entries e ON e.folder_id=f.id AND e.deleted=0 WHERE f.user_id=? GROUP BY f.id ORDER BY f.name",(uid,)).fetchall()
    return render_template("folders.html",folders=items)

@app.route("/folder/<int:fid>/delete",methods=["POST"])
@login_required
def delete_folder(fid):
    with get_db() as db:
        f=db.execute("SELECT id FROM folders WHERE id=? AND user_id=?",(fid,session["user_id"])).fetchone()
        if f: db.execute("UPDATE diary_entries SET folder_id=NULL WHERE folder_id=?",(fid,)); db.execute("DELETE FROM folders WHERE id=?",(fid,))
    return redirect(url_for("folders"))

@app.route("/mood",methods=["GET","POST"])
@login_required
def mood_page():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            db.execute("INSERT INTO moods(user_id,mood,note,mood_date) VALUES(?,?,?,?) ON CONFLICT(user_id,mood_date) DO UPDATE SET mood=excluded.mood,note=excluded.note",
                (uid,request.form.get("mood"),request.form.get("note",""),request.form.get("mood_date") or date.today().isoformat()))
        logs=db.execute("SELECT * FROM moods WHERE user_id=? ORDER BY mood_date DESC LIMIT 60",(uid,)).fetchall()
    return render_template("mood.html",logs=logs)

@app.route("/memories",methods=["GET","POST"])
@login_required
def memories():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            title=request.form.get("title","").strip()
            if title:
                f=request.files.get("file"); filename=""
                if f and f.filename:
                    ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
                    if ext in ALLOWED:
                        filename=secrets.token_hex(12)+"."+ext; f.save(Path(UPLOAD_FOLDER)/filename)
                db.execute("INSERT INTO memories(user_id,title,caption,memory_date,location,filename) VALUES(?,?,?,?,?,?)",
                    (uid,title,request.form.get("caption",""),request.form.get("memory_date") or date.today().isoformat(),request.form.get("location",""),filename))
        items=db.execute("SELECT * FROM memories WHERE user_id=? ORDER BY memory_date DESC",(uid,)).fetchall()
    return render_template("memories.html",memories=items)

@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    with get_db() as db:
        ok=db.execute("SELECT id FROM memories WHERE user_id=? AND filename=?",(session["user_id"],filename)).fetchone()
    if not ok: abort(404)
    return send_from_directory(UPLOAD_FOLDER,filename)

@app.route("/calendar",methods=["GET","POST"])
@login_required
def calendar():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            title=request.form.get("title","").strip()
            if title: db.execute("INSERT INTO calendar_events(user_id,title,event_date,description) VALUES(?,?,?,?)",(uid,title,request.form.get("event_date") or date.today().isoformat(),request.form.get("description","")))
        events=db.execute("SELECT * FROM calendar_events WHERE user_id=? ORDER BY event_date",(uid,)).fetchall()
        entries=db.execute("SELECT id,title,entry_date FROM diary_entries WHERE user_id=? AND deleted=0 ORDER BY entry_date DESC LIMIT 100",(uid,)).fetchall()
    return render_template("calendar.html",events=events,entries=entries)

@app.route("/goals",methods=["GET","POST"])
@login_required
def goals():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            title=request.form.get("title","").strip()
            if title: db.execute("INSERT INTO goals(user_id,title,description,target_date) VALUES(?,?,?,?)",(uid,title,request.form.get("description",""),request.form.get("target_date","")))
        items=db.execute("SELECT * FROM goals WHERE user_id=? ORDER BY completed,target_date",(uid,)).fetchall()
    return render_template("goals.html",goals=items)

@app.route("/goal/<int:gid>/progress",methods=["POST"])
@login_required
def goal_progress(gid):
    try: p=max(0,min(100,int(request.form.get("progress",0))))
    except: p=0
    with get_db() as db: db.execute("UPDATE goals SET progress=?,completed=? WHERE id=? AND user_id=?",(p,1 if p==100 else 0,gid,session["user_id"]))
    return redirect(url_for("goals"))

@app.route("/bucket",methods=["GET","POST"])
@login_required
def bucket():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            title=request.form.get("title","").strip()
            if title: db.execute("INSERT INTO bucket_list(user_id,title,category,target_date) VALUES(?,?,?,?)",(uid,title,request.form.get("category","Personal"),request.form.get("target_date","")))
        items=db.execute("SELECT * FROM bucket_list WHERE user_id=? ORDER BY completed,target_date",(uid,)).fetchall()
    return render_template("bucket.html",items=items)

@app.route("/bucket/<int:item_id>/toggle",methods=["POST"])
@login_required
def bucket_toggle(item_id):
    with get_db() as db: db.execute("UPDATE bucket_list SET completed=1-completed WHERE id=? AND user_id=?",(item_id,session["user_id"]))
    return redirect(url_for("bucket"))

@app.route("/prompts")
@login_required
def prompts():
    prompts=["What made you happy today?","What did you learn today?","What is one memory you never want to forget?","What are you looking forward to?","What would you tell your younger self?","Who made a positive difference in your life recently?","What is one small thing you are grateful for?"]
    return render_template("prompts.html",prompts=prompts)

@app.route("/statistics")
@login_required
def statistics():
    uid=session["user_id"]
    with get_db() as db:
        total=db.execute("SELECT COUNT(*) n FROM diary_entries WHERE user_id=? AND deleted=0",(uid,)).fetchone()["n"]
        words=db.execute("SELECT COALESCE(SUM(LENGTH(content)-LENGTH(REPLACE(content,' ',''))+1),0) n FROM diary_entries WHERE user_id=? AND deleted=0",(uid,)).fetchone()["n"]
        bymonth=db.execute("SELECT substr(entry_date,1,7) month,COUNT(*) count FROM diary_entries WHERE user_id=? AND deleted=0 GROUP BY month ORDER BY month DESC LIMIT 12",(uid,)).fetchall()
        bymood=db.execute("SELECT mood,COUNT(*) count FROM moods WHERE user_id=? GROUP BY mood ORDER BY count DESC",(uid,)).fetchall()
    return render_template("statistics.html",total=total,words=words,bymonth=bymonth,bymood=bymood)

@app.route("/settings",methods=["GET","POST"])
@login_required
def settings():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            theme=request.form.get("theme","light")
            db.execute("UPDATE user_settings SET theme=? WHERE user_id=?",(theme,uid))
            flash("Appearance saved.","success")
        user=db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        setting=db.execute("SELECT * FROM user_settings WHERE user_id=?",(uid,)).fetchone()
    return render_template("settings.html",user=user,theme=setting["theme"])

@app.route("/profile",methods=["POST"])
@login_required
def profile():
    name=request.form.get("name","").strip(); gender=request.form.get("gender","Prefer not to say")
    if name:
        with get_db() as db: db.execute("UPDATE users SET name=?,gender=? WHERE id=?",(name,gender,session["user_id"]))
    return redirect(url_for("settings"))

@app.route("/share/<int:entry_id>",methods=["POST"])
@login_required
def share_entry(entry_id):
    uid=session["user_id"]
    with get_db() as db:
        if not owned_entry(db,entry_id,uid): abort(404)
        token=secrets.token_urlsafe(32)
        db.execute("INSERT INTO shares(entry_id,owner_id,token,expires_on) VALUES(?,?,?,?)",(entry_id,uid,token,request.form.get("expires_on","")))
    return render_template("share_created.html",url=request.host_url+"s/"+token)

@app.route("/s/<token>")
def public_share(token):
    with get_db() as db:
        row=db.execute("""SELECT e.title,e.content,e.entry_date,u.name,s.expires_on FROM shares s JOIN diary_entries e ON e.id=s.entry_id JOIN users u ON u.id=s.owner_id WHERE s.token=? AND e.deleted=0""",(token,)).fetchone()
    if not row: abort(404)
    if row["expires_on"] and row["expires_on"] < date.today().isoformat(): abort(404)
    return render_template("public_share.html",entry=row)

@app.route("/share/revoke/<int:sid>",methods=["POST"])
@login_required
def revoke_share(sid):
    with get_db() as db: db.execute("DELETE FROM shares WHERE id=? AND owner_id=?",(sid,session["user_id"]))
    return redirect(url_for("privacy"))

@app.route("/privacy")
@login_required
def privacy():
    with get_db() as db: shares=db.execute("SELECT s.id,s.created_at,e.title FROM shares s JOIN diary_entries e ON e.id=s.entry_id WHERE s.owner_id=?",(session["user_id"],)).fetchall()
    return render_template("privacy.html",shares=shares)

@app.route("/export")
@login_required
def export_data():
    uid=session["user_id"]
    with get_db() as db:
        data={}
        for table in ["users","diary_entries","folders","moods","memories","calendar_events","goals","bucket_list"]:
            rows=db.execute(f"SELECT * FROM {table} WHERE user_id=?",(uid,)).fetchall()
            data[table]=[dict(r) for r in rows]
        data["users"][0].pop("password_hash",None)
    response=make_response(json.dumps(data,ensure_ascii=False,indent=2)); response.headers["Content-Type"]="application/json"
    response.headers["Content-Disposition"]="attachment; filename=my-diary-backup.json"
    return response

@app.route("/import",methods=["GET","POST"])
@login_required
def import_data():
    if request.method=="POST":
        f=request.files.get("backup")
        try:
            data=json.load(f)
            uid=session["user_id"]
            with get_db() as db:
                for row in data.get("diary_entries",[]):
                    db.execute("INSERT INTO diary_entries(user_id,title,content,entry_date,category,mood,tags) VALUES(?,?,?,?,?,?,?)",
                        (uid,row.get("title","Imported entry"),row.get("content",""),row.get("entry_date",date.today().isoformat()),row.get("category","Personal Life"),row.get("mood",""),row.get("tags","")))
            flash("Backup entries imported.","success")
        except Exception: flash("That backup could not be read. Please choose a valid diary JSON export.","error")
        return redirect(url_for("import_data"))
    return render_template("import_export.html")

@app.route("/reminders",methods=["GET","POST"])
@login_required
def reminders():
    uid=session["user_id"]
    with get_db() as db:
        if request.method=="POST":
            title=request.form.get("title","").strip()
            if title: db.execute("INSERT INTO reminders(user_id,title,remind_date) VALUES(?,?,?)",(uid,title,request.form.get("remind_date") or date.today().isoformat()))
        rows=db.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY remind_date",(uid,)).fetchall()
    return render_template("reminders.html",reminders=rows)

@app.route("/entry/<int:entry_id>/share",methods=["GET"])
@login_required
def share_form(entry_id):
    with get_db() as db:
        if not owned_entry(db,entry_id,session["user_id"]): abort(404)
    return render_template("share_form.html",entry_id=entry_id)

@app.errorhandler(413)
def too_large(e): return "Upload too large. Maximum file size is 25 MB.",413

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
