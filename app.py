"""
ImpactStream — Ministère Apostolique Impact
Backend Flask avec SQLite, Administration & Upload
"""

from flask import (
    Flask, render_template, jsonify, request,
    redirect, url_for, session, flash, send_from_directory,
    make_response, send_file
)
from werkzeug.utils import secure_filename
import sqlite3
import os
import uuid
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
import io
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'impactstream-secret-key-2026-mai'

@app.after_request
def add_header(response):
    """Empêcher le cache du navigateur pour éviter que l'historique ne re-connecte l'utilisateur après déconnexion."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'eglise_media.db')

# Dossiers d'upload
UPLOAD_VIDEOS = os.path.join(BASE_DIR, 'static', 'videos')
UPLOAD_IMAGES = os.path.join(BASE_DIR, 'static', 'images')

# Extensions autorisées
ALLOWED_VIDEO = {'.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv'}
ALLOWED_AUDIO = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'}
ALLOWED_IMAGE = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

# Code d'accès adminn
ADMIN_CODE = 'Bungudi128'

# Commissions autorisées
COMMISSIONS_ENSEIGNEMENT = ['Intercession', 'Couples', 'HED', 'Johanna', 'Joseph', 'PDS', 'Culte', 'Nayoth', 'FGI', 'Enfants']
COMMISSIONS_MUSIQUE = ['Adoration MP3', 'Adoration MP4', 'Nayoth', 'Chants PAB', 'Instrumentale']
CATEGORIES = ['Enseignement', 'Musique', 'Podcast']

# Fuseau horaire de référence pour la prise de présence (Bruxelles)
# Toutes les heures de présence seront calculées en heure de Bruxelles,
# peu importe d'où les utilisateurs se connectent dans le monde.
ATTENDANCE_TZ = ZoneInfo('Europe/Brussels')

# Créer les dossiers s'ils n'existent pas
os.makedirs(UPLOAD_VIDEOS, exist_ok=True)
os.makedirs(UPLOAD_IMAGES, exist_ok=True)


# ─────────────────────────────────────────────
# Base de données
# ─────────────────────────────────────────────

# Déploiement test - vérification de la persistance de la base de données
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    sqlite3.IntegrityError = psycopg2.IntegrityError

class PostgresRowWrapper:
    def __init__(self, dict_row):
        self._row = dict_row

    def __getitem__(self, key):
        if isinstance(key, int):
            val = list(self._row.values())[key]
        else:
            val = self._row[key]
        
        from datetime import datetime, date
        if isinstance(val, datetime):
            return val.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(val, date):
            return val.strftime('%Y-%m-%d')
        return val

    def keys(self):
        return list(self._row.keys())

    def __iter__(self):
        return iter(self._row.keys())

    def __len__(self):
        return len(self._row)

    def items(self):
        return [(k, self[k]) for k in self.keys()]

class PostgresCursorWrapper:
    def __init__(self, pg_cursor):
        self.cursor = pg_cursor
        self._lastrowid = None

    def execute(self, query, params=None):
        if isinstance(query, str):
            query = query.replace('?', '%s')
            if 'INSERT OR IGNORE' in query.upper():
                query = query.replace('INSERT OR IGNORE', 'INSERT') + ' ON CONFLICT DO NOTHING'
            elif 'INSERT OR REPLACE' in query.upper():
                query = query.replace('INSERT OR REPLACE', 'INSERT') + ' ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value'
            
            is_insert = query.strip().upper().startswith('INSERT')
            if is_insert and 'RETURNING' not in query.upper():
                if 'INTO SETTINGS' not in query.upper():
                    query += ' RETURNING id'
        else:
            is_insert = False

        self.cursor.execute(query, params)

        if is_insert and 'INTO SETTINGS' not in query.upper():
            try:
                row = self.cursor.fetchone()
                if row:
                    self._lastrowid = row[0]
            except Exception:
                self._lastrowid = None
        else:
            self._lastrowid = None

    def fetchone(self):
        try:
            res = self.cursor.fetchone()
            if res is None:
                return None
            return PostgresRowWrapper(res)
        except Exception:
            return None

    def fetchall(self):
        try:
            rows = self.cursor.fetchall()
            return [PostgresRowWrapper(r) for r in rows]
        except Exception:
            return []

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        return self._lastrowid

    def close(self):
        self.cursor.close()

class PostgresConnectionWrapper:
    def __init__(self, pg_conn):
        self.conn = pg_conn

    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor(cursor_factory=RealDictCursor))

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

def get_db():
    """Obtenir une connexion à la base de données (SQLite en local, PostgreSQL/Supabase en prod)."""
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        wrapper = PostgresConnectionWrapper(conn)
        
        # Auto-migration PostgreSQL (salle d'attente & expulsion)
        try:
            cur = wrapper.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS waiting_room (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Garantir la présence de la configuration de la salle d'attente
            cur.execute("INSERT INTO settings (key, value) VALUES ('waiting_room_enabled', '0') ON CONFLICT (key) DO NOTHING")
            # Ajouter la colonne is_evicted à la table attendance si elle n'existe pas
            cur.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS is_evicted INTEGER DEFAULT 0")
            wrapper.commit()
        except Exception as e:
            wrapper.rollback()
            print(f"⚠️ Erreur auto-migration PostgreSQL: {e}")
            
        return wrapper

    db_exists = os.path.exists(DATABASE)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()


    # Création table medias
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titre TEXT NOT NULL,
            description TEXT,
            categorie TEXT,
            commission TEXT,
            langue TEXT,
            url_miniature TEXT,
            url_video TEXT,
            url_video_en TEXT,
            url_video_es TEXT,
            url_video_nl TEXT,
            url_video_ln TEXT,
            paroles_keywords TEXT,
            position_banniere INTEGER DEFAULT 15,
            series_id INTEGER,
            chapitre TEXT,
            ordre_episode INTEGER,
            date_ajout TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Création table users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            acces_nayoth INTEGER DEFAULT 0,
            acces_intercession INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            verification_token TEXT,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Création table invitations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            token TEXT UNIQUE NOT NULL,
            used INTEGER DEFAULT 0,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Création table communiques
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS communiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titre TEXT NOT NULL,
            contenu_fr TEXT NOT NULL,
            contenu_en TEXT,
            contenu_es TEXT,
            contenu_nl TEXT,
            contenu_ln TEXT,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Création table series
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titre TEXT NOT NULL,
            description TEXT,
            categorie TEXT NOT NULL,
            commission TEXT,
            url_miniature TEXT,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Création table settings
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('radio_enabled', '0'))
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('radio_stream_url', 'https://icecast.radiofrance.fr/fip-midfi.mp3'))
    
    # Création table live_streams
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titre TEXT NOT NULL,
            description TEXT,
            url_direct TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Création table quizzes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titre TEXT NOT NULL,
            description TEXT,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Création table quiz_questions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            question_text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            correct_option INTEGER NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        )
    ''')

    # Création table attendance (prise de présence)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            display_name TEXT NOT NULL,
            login_at TIMESTAMP NOT NULL,
            is_late INTEGER DEFAULT 0,
            date_key TEXT NOT NULL,
            UNIQUE(user_email, date_key)
        )
    ''')

    # Settings pour la prise de présence
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('attendance_enabled', '0'))
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('attendance_target_hour', '09:00'))

    # Création table waiting_room
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS waiting_room (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Settings pour la salle d'attente
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('waiting_room_enabled', '0'))

    # Auto-migration SQLite pour attendance (is_evicted)
    try:
        cursor.execute("ALTER TABLE attendance ADD COLUMN is_evicted INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()

    # Auto-migration medias
    cursor.execute('PRAGMA table_info(medias)')
    columns = [row[1] for row in cursor.fetchall()]
    
    migrated = False
    if 'url_video_en' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN url_video_en TEXT')
        migrated = True
    if 'url_video_es' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN url_video_es TEXT')
        migrated = True
    if 'url_video_nl' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN url_video_nl TEXT')
        migrated = True
    if 'url_video_ln' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN url_video_ln TEXT')
        migrated = True
    if 'paroles_keywords' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN paroles_keywords TEXT')
        migrated = True
    if 'position_banniere' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN position_banniere INTEGER DEFAULT 15')
        migrated = True
    if 'series_id' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN series_id INTEGER')
        migrated = True
    if 'chapitre' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN chapitre TEXT')
        migrated = True
    if 'ordre_episode' not in columns:
        cursor.execute('ALTER TABLE medias ADD COLUMN ordre_episode INTEGER')
        migrated = True
        
    if migrated:
        conn.commit()
        print("⚙️ Migration SQLite : Colonnes de langues, paroles, position banniere et series ajoutées.")

    # Auto-migration users permissions
    cursor.execute('PRAGMA table_info(users)')
    user_columns = [row[1] for row in cursor.fetchall()]
    
    user_migrated = False
    if 'acces_nayoth' not in user_columns:
        cursor.execute('ALTER TABLE users ADD COLUMN acces_nayoth INTEGER DEFAULT 0')
        user_migrated = True
    if 'acces_intercession' not in user_columns:
        cursor.execute('ALTER TABLE users ADD COLUMN acces_intercession INTEGER DEFAULT 0')
        user_migrated = True
    if 'is_verified' not in user_columns:
        cursor.execute('ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0')
        user_migrated = True
    if 'verification_token' not in user_columns:
        cursor.execute('ALTER TABLE users ADD COLUMN verification_token TEXT')
        user_migrated = True
        
    if user_migrated:
        conn.commit()
        print("⚙️ Migration SQLite : Colonnes de permissions d'accès et de vérification utilisateur ajoutées.")

    # Créer un compte utilisateur par défaut si vide
    cursor.execute('SELECT COUNT(*) FROM users')
    user_count = cursor.fetchone()[0]
    if user_count == 0:
        default_email = 'yann.noukaze@ministereimpact.org'
        default_password_hash = generate_password_hash('Bungudi128')
        cursor.execute('INSERT INTO users (email, password_hash, acces_nayoth, acces_intercession, is_verified) VALUES (?, ?, 1, 1, 1)', (default_email, default_password_hash))
        conn.commit()
        print(f"👤 Compte par défaut créé : {default_email} / Bungudi128!")
    else:
        # S'assurer que Yann a bien tous les accès et est activé
        cursor.execute('UPDATE users SET acces_nayoth = 1, acces_intercession = 1, is_verified = 1 WHERE email = ?', ('yann.noukaze@ministereimpact.org',))
        conn.commit()

    return conn


def init_db():
    """Initialiser la base de données."""
    conn = get_db()
    conn.close()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def login_required(f):
    """Décorateur pour protéger les routes admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


def user_login_required(f):
    """Décorateur pour exiger que l'utilisateur soit connecté au site public."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        email = session.get('user_email')
        if not session.get('user_logged_in') or not email:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'unauthorized'}), 401
            return redirect(url_for('user_login'))

        # Vérifier si l'utilisateur est expulsé aujourd'hui
        try:
            conn = get_db()
            today_key = datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d')
            row = conn.execute(
                'SELECT is_evicted FROM attendance WHERE user_email = ? AND date_key = ?',
                (email, today_key)
            ).fetchone()
            conn.close()
            if row and row['is_evicted'] == 1:
                session.pop('user_logged_in', None)
                session.pop('user_email', None)
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'evicted'}), 401
                flash("⚠️ Vous avez été déconnecté par un administrateur.", "error")
                return redirect(url_for('user_login'))
        except Exception as e:
            print(f"⚠️ Erreur vérification expulsion: {e}")

        return f(*args, **kwargs)
    return decorated_function


def save_upload(file, folder, allowed_extensions):
    """Sauvegarder un fichier uploadé avec un nom unique."""
    if not file or file.filename == '':
        return None

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        return None

    # Générer un nom unique
    unique_name = f"{uuid.uuid4().hex[:12]}_{secure_filename(file.filename)}"
    filepath = os.path.join(folder, unique_name)
    file.save(filepath)

    # Retourner le chemin relatif pour la BDD
    rel_folder = 'videos' if 'videos' in folder else 'images'
    return f'/static/{rel_folder}/{unique_name}'


def get_file_size_mb(filepath):
    """Obtenir la taille d'un fichier en MB."""
    try:
        full_path = os.path.join(BASE_DIR, filepath.lstrip('/'))
        size = os.path.getsize(full_path)
        return round(size / (1024 * 1024), 1)
    except Exception:
        return 0



R2_PUBLIC_URL = os.environ.get('R2_PUBLIC_URL', 'https://pub-518d7a54eb024008841fbc5cddb7b0a4.r2.dev')

@app.before_request
def redirect_missing_media_to_r2():
    path = request.path
    if path.startswith('/static/images/'):
        relative_path = path[len('/static/images/'):]
        local_path = os.path.join(app.root_path, 'static', 'images', relative_path)
        if not os.path.exists(local_path) and R2_PUBLIC_URL:
            return redirect(f"{R2_PUBLIC_URL.rstrip('/')}/{relative_path}")
    elif path.startswith('/static/videos/'):
        relative_path = path[len('/static/videos/'):]
        local_path = os.path.join(app.root_path, 'static', 'videos', relative_path)
        if not os.path.exists(local_path) and R2_PUBLIC_URL:
            return redirect(f"{R2_PUBLIC_URL.rstrip('/')}/video/{relative_path}")


# ─────────────────────────────────────────────
# Routes d'authentification utilisateur
# ─────────────────────────────────────────────


@app.route('/login', methods=['GET', 'POST'])
def user_login():
    """Connexion utilisateur restreinte au domaine ministereimpact.org avec invitations."""
    if session.get('user_logged_in'):
        return redirect(url_for('index'))

    invite_token = request.args.get('token', '').strip()
    view = request.args.get('view', '').strip()

    invite_email = None
    valid_invite_token = None

    if invite_token:
        conn = get_db()
        invite = conn.execute('SELECT * FROM invitations WHERE token = ? AND used = 0', (invite_token,)).fetchone()
        conn.close()
        if invite:
            invite_email = invite['email']
            valid_invite_token = invite_token
        else:
            flash("Ce lien d'invitation est invalide ou a déjà été utilisé.", "error")
            return redirect(url_for('user_login'))
    elif view == 'register':
        flash("L'inscription nécessite un lien d'invitation unique.", "error")
        return redirect(url_for('user_login'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()

        if not email or not password:
            flash('Veuillez remplir tous les champs.', 'error')
            return redirect(url_for('user_login'))

        # Étape 1 : Restriction stricte au domaine
        parts = email.split('@')
        if len(parts) != 2 or parts[1] != 'ministereimpact.org':
            flash('Domaine non autorisé', 'error')
            return redirect(url_for('user_login'))

        # Étape 2 : Recherche en base
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        # Étape 3 : Comparaison mot de passe
        if user and check_password_hash(user['password_hash'], password):
            # Étape 4 : Vérification d'activation par email
            if not user['is_verified']:
                activation_link = f"{request.host_url.rstrip('/')}/verify-email?token={user['verification_token']}"
                flash(f"⚠️ Votre adresse e-mail n'est pas encore vérifiée. <a href='{activation_link}' style='text-decoration: underline; font-weight: bold;'>Cliquez ici pour l'activer temporairement ({email})</a>", "error")
                return redirect(url_for('user_login'))

            # Vérification salle d'attente
            conn_wr = get_db()
            wr_setting = conn_wr.execute("SELECT value FROM settings WHERE key = 'waiting_room_enabled'").fetchone()
            waiting_enabled = wr_setting['value'] == '1' if wr_setting else False
            
            if waiting_enabled:
                # Vérifier si l'utilisateur a déjà une demande
                existing = conn_wr.execute("SELECT * FROM waiting_room WHERE email = ?", (email,)).fetchone()
                if not existing:
                    conn_wr.execute("INSERT INTO waiting_room (email, status) VALUES (?, 'pending')", (email,))
                    conn_wr.commit()
                elif existing['status'] == 'approved':
                    # Déjà approuvé, on le laisse passer
                    pass
                else:
                    # Si c'était rejeté ou en cours, on s'assure qu'il repasse en pending
                    conn_wr.execute("UPDATE waiting_room SET status = 'pending' WHERE email = ?", (email,))
                    conn_wr.commit()

                # S'il n'est pas déjà approved, on le redirige vers la salle d'attente
                if not existing or existing['status'] != 'approved':
                    session['waiting_for_approval'] = email
                    conn_wr.close()
                    return redirect(url_for('user_waiting_room'))
            
            conn_wr.close()

            # Réinitialiser l'expulsion s'ils se reconnectent
            try:
                conn_ev = get_db()
                today_key = datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d')
                conn_ev.execute(
                    'UPDATE attendance SET is_evicted = 0 WHERE user_email = ? AND date_key = ?',
                    (email, today_key)
                )
                conn_ev.commit()
                conn_ev.close()
            except Exception as e:
                print(f"⚠️ Erreur réinitialisation expulsion: {e}")
                
            session['user_logged_in'] = True
            session['user_email'] = email

            # ── Prise de présence automatique ──
            try:
                conn2 = get_db()
                att_enabled = conn2.execute("SELECT value FROM settings WHERE key = 'attendance_enabled'").fetchone()
                if att_enabled and att_enabled['value'] == '1':
                    now = datetime.now(ATTENDANCE_TZ)
                    date_key = now.strftime('%Y-%m-%d')
                    # Anti-doublon : vérifier si déjà enregistré aujourd'hui
                    existing = conn2.execute(
                        'SELECT id FROM attendance WHERE user_email = ? AND date_key = ?',
                        (email, date_key)
                    ).fetchone()
                    if not existing:
                        # Extraire le nom depuis l'email (prenom.nom@ministereimpact.org)
                        local_part = email.split('@')[0]  # prenom.nom
                        parts = local_part.split('.')
                        display_name = ' '.join(p.capitalize() for p in parts)
                        # Déterminer si en retard (gère le passage de minuit)
                        target_row = conn2.execute("SELECT value FROM settings WHERE key = 'attendance_target_hour'").fetchone()
                        target_hour_str = target_row['value'] if target_row else '09:00'
                        t_parts = target_hour_str.split(':')
                        target_h, target_m = int(t_parts[0]), int(t_parts[1])
                        login_minutes = now.hour * 60 + now.minute
                        target_minutes = target_h * 60 + target_m
                        # Delta en minutes (modulo 24h) : si entre 1 et 720 min après la cible → en retard
                        delta = (login_minutes - target_minutes) % (24 * 60)
                        is_late = 1 if (0 < delta <= 720) else 0
                        conn2.execute(
                            'INSERT INTO attendance (user_email, display_name, login_at, is_late, date_key) VALUES (?, ?, ?, ?, ?)',
                            (email, display_name, now.strftime('%Y-%m-%d %H:%M:%S'), is_late, date_key)
                        )
                        conn2.commit()
                conn2.close()
            except Exception as e:
                print(f'⚠️ Erreur prise de présence: {e}')

            return redirect(url_for('index'))
        else:
            flash('Identifiants incorrects.', 'error')
            return redirect(url_for('user_login'))

    return render_template(
        'login.html',
        invite_email=invite_email,
        invite_token=valid_invite_token
    )


@app.route('/logout')
def user_logout():
    """Déconnexion utilisateur."""
    session.pop('user_logged_in', None)
    session.pop('user_email', None)
    session.pop('waiting_for_approval', None)
    return redirect(url_for('user_login'))


# ─────────────────────────────────────────────
# Salle d'Attente
# ─────────────────────────────────────────────

@app.route('/waiting-room')
def user_waiting_room():
    """Affiche la salle d'attente."""
    email = session.get('waiting_for_approval')
    if not email:
        return redirect(url_for('user_login'))
    return render_template('waiting_room.html', email=email)


@app.route('/api/waiting-status')
def api_waiting_status():
    """API publique pour vérifier le statut d'approbation d'un utilisateur."""
    email = session.get('waiting_for_approval')
    if not email:
        return jsonify({'status': 'none'}), 400
        
    conn = get_db()
    entry = conn.execute("SELECT status FROM waiting_room WHERE email = ?", (email,)).fetchone()
    status = entry['status'] if entry else 'pending'
    
    if status == 'approved':
        # Connexion complète de l'utilisateur
        session['user_logged_in'] = True
        session['user_email'] = email
        session.pop('waiting_for_approval', None)
        
        # Enregistrer la présence au culte (prise de présence automatique)
        try:
            att_enabled = conn.execute("SELECT value FROM settings WHERE key = 'attendance_enabled'").fetchone()
            if att_enabled and att_enabled['value'] == '1':
                now = datetime.now(ATTENDANCE_TZ)
                date_key = now.strftime('%Y-%m-%d')
                existing = conn.execute(
                    'SELECT id FROM attendance WHERE user_email = ? AND date_key = ?',
                    (email, date_key)
                ).fetchone()
                if not existing:
                    local_part = email.split('@')[0]
                    parts = local_part.split('.')
                    display_name = ' '.join(p.capitalize() for p in parts)
                    
                    target_row = conn.execute("SELECT value FROM settings WHERE key = 'attendance_target_hour'").fetchone()
                    target_hour_str = target_row['value'] if target_row else '09:00'
                    t_parts = target_hour_str.split(':')
                    target_h, target_m = int(t_parts[0]), int(t_parts[1])
                    login_minutes = now.hour * 60 + now.minute
                    target_minutes = target_h * 60 + target_m
                    delta = (login_minutes - target_minutes) % (24 * 60)
                    is_late = 1 if (0 < delta <= 720) else 0
                    
                    conn.execute(
                        'INSERT INTO attendance (user_email, display_name, login_at, is_late, date_key) VALUES (?, ?, ?, ?, ?)',
                        (email, display_name, now.strftime('%Y-%m-%d %H:%M:%S'), is_late, date_key)
                    )
                    conn.commit()
        except Exception as e:
            print(f"⚠️ Erreur prise de présence dans la salle d'attente: {e}")
            
    conn.close()
    return jsonify({'status': status})


# ─────────────────────────────────────────────
# Routes publiques
# ─────────────────────────────────────────────

@app.route('/')
@user_login_required
def index():
    """Page d'accueil avec le catalogue de médias filtré selon les accès."""
    # Déconnexion automatique quand on retourne sur le site public
    session.pop('admin_logged_in', None)
    
    conn = get_db()
    
    # Récupérer l'accès de l'utilisateur
    user_email = session.get('user_email')
    user_row = conn.execute('SELECT acces_nayoth, acces_intercession FROM users WHERE email = ?', (user_email,)).fetchone()
    has_nayoth = user_row['acces_nayoth'] if user_row else 0
    has_intercession = user_row['acces_intercession'] if user_row else 0

    # Construire la requête SQL avec restrictions de sécurité
    query = 'SELECT * FROM medias WHERE 1=1'
    if not has_nayoth:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Nayoth')"
    if not has_intercession:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Intercession')"
    
    # Exclure le FGI et les Enfants du catalogue général
    query += " AND (commission IS NULL OR (commission != 'FGI' AND commission != 'Enfants'))"
    
    # Exclure les épisodes appartenant à une série du catalogue général
    query += " AND series_id IS NULL"
    
    query += ' ORDER BY id DESC'

    medias = conn.execute(query).fetchall()
    featured = medias[0] if medias else None

    langues = conn.execute('SELECT DISTINCT langue FROM medias ORDER BY langue').fetchall()
    conn.close()

    # Filtrer dynamiquement la liste des sous-sections d'enseignements visibles
    filtered_commissions_enseignement = list(COMMISSIONS_ENSEIGNEMENT)
    if not has_nayoth:
        filtered_commissions_enseignement.remove('Nayoth')
    if not has_intercession:
        filtered_commissions_enseignement.remove('Intercession')
    if 'Enfants' in filtered_commissions_enseignement:
        filtered_commissions_enseignement.remove('Enfants')

    return render_template(
        'index.html',
        medias=[dict(m) for m in medias],
        featured=dict(featured) if featured else None,
        commissions_enseignement=filtered_commissions_enseignement,
        commissions_musique=COMMISSIONS_MUSIQUE,
        langues=[l['langue'] for l in langues],
        has_nayoth=has_nayoth,
        has_intercession=has_intercession
    )


@app.route('/api/session-status')
@user_login_required
def api_session_status():
    """Vérifier rapidement si la session de l'utilisateur est toujours valide et non expulsée."""
    return jsonify({'status': 'ok'})


@app.route('/api/medias')
@user_login_required
def api_medias():
    """API pour la recherche et le filtrage dynamique des médias."""
    search = request.args.get('search', '').strip()
    commission = request.args.get('commission', '').strip()
    langue = request.args.get('langue', '').strip()
    categorie = request.args.get('categorie', '').strip()

    conn = get_db()
    
    # Récupérer l'accès de l'utilisateur
    user_email = session.get('user_email')
    user_row = conn.execute('SELECT acces_nayoth, acces_intercession FROM users WHERE email = ?', (user_email,)).fetchone()
    has_nayoth = user_row['acces_nayoth'] if user_row else 0
    has_intercession = user_row['acces_intercession'] if user_row else 0

    query = 'SELECT * FROM medias WHERE 1=1'
    params = []

    # Filtrage de sécurité
    if not has_nayoth:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Nayoth')"
    if not has_intercession:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Intercession')"

    # Exclure le FGI du catalogue général/recherche, sauf si explicitement demandé
    if commission != 'FGI':
        query += " AND (commission IS NULL OR commission != 'FGI')"

    # Exclure Enfants du catalogue général/recherche, sauf si explicitement demandé
    if commission != 'Enfants':
        query += " AND (commission IS NULL OR commission != 'Enfants')"

    # Exclure les épisodes appartenant à une série
    query += " AND series_id IS NULL"

    if search:
        query += ' AND (titre LIKE ? OR description LIKE ? OR paroles_keywords LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    if commission:
        query += ' AND commission = ?'
        params.append(commission)
    if langue:
        query += ' AND langue = ?'
        params.append(langue)
    if categorie:
        query += ' AND categorie = ?'
        params.append(categorie)

    query += ' ORDER BY id DESC'
    medias = conn.execute(query, params).fetchall()
    conn.close()

    return jsonify([dict(m) for m in medias])


@app.route('/api/media/<int:media_id>')
@user_login_required
def api_media_detail(media_id):
    """Récupérer les détails d'un média spécifique avec filtrage de sécurité."""
    conn = get_db()
    media = conn.execute('SELECT * FROM medias WHERE id = ?', (media_id,)).fetchone()
    
    if not media:
        conn.close()
        return jsonify({'error': 'Média non trouvé'}), 404

    # Récupérer l'accès de l'utilisateur pour valider la permission
    user_email = session.get('user_email')
    user_row = conn.execute('SELECT acces_nayoth, acces_intercession FROM users WHERE email = ?', (user_email,)).fetchone()
    conn.close()
    
    has_nayoth = user_row['acces_nayoth'] if user_row else 0
    has_intercession = user_row['acces_intercession'] if user_row else 0

    # Vérification stricte
    if media['categorie'] == 'Enseignement':
        if media['commission'] == 'Nayoth' and not has_nayoth:
            return jsonify({'error': 'Accès interdit'}), 403
        if media['commission'] == 'Intercession' and not has_intercession:
            return jsonify({'error': 'Accès interdit'}), 403

    return jsonify(dict(media))


# ─────────────────────────────────────────────
# Routes Admin — Authentification
# ─────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Page de connexion à l'espace admin."""
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if code == ADMIN_CODE:
            session['admin_logged_in'] = True
            session.permanent = False
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Code d\'accès incorrect.', 'error')

    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    """Déconnexion de l'espace admin."""
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))


# ─────────────────────────────────────────────
# Routes Admin — Dashboard & CRUD
# ─────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin_dashboard():
    """Tableau de bord administrateur."""
    conn = get_db()
    medias = conn.execute('SELECT * FROM medias ORDER BY id DESC').fetchall()
    users = conn.execute('SELECT * FROM users ORDER BY id DESC').fetchall()
    invitations = conn.execute('SELECT * FROM invitations ORDER BY id DESC').fetchall()
    communiques = conn.execute('SELECT * FROM communiques ORDER BY id DESC').fetchall()

    # Statistiques
    stats = {
        'total': len(medias),
        'enseignements': len([m for m in medias if m['categorie'] == 'Enseignement']),
        'musiques': len([m for m in medias if m['categorie'] == 'Musique']),
        'podcasts': len([m for m in medias if m['categorie'] == 'Podcast']),
    }

    series = conn.execute('SELECT * FROM series ORDER BY id DESC').fetchall()
    lives = conn.execute('SELECT * FROM live_streams ORDER BY id DESC').fetchall()
    
    # Récupérer la configuration radio
    radio_enabled = conn.execute("SELECT value FROM settings WHERE key = 'radio_enabled'").fetchone()
    radio_stream_url = conn.execute("SELECT value FROM settings WHERE key = 'radio_stream_url'").fetchone()
    radio_config = {
        'enabled': radio_enabled['value'] if radio_enabled else '0',
        'stream_url': radio_stream_url['value'] if radio_stream_url else ''
    }

    # Données de prise de présence
    att_enabled_row = conn.execute("SELECT value FROM settings WHERE key = 'attendance_enabled'").fetchone()
    att_hour_row = conn.execute("SELECT value FROM settings WHERE key = 'attendance_target_hour'").fetchone()
    attendance_config = {
        'enabled': att_enabled_row['value'] if att_enabled_row else '0',
        'target_hour': att_hour_row['value'] if att_hour_row else '09:00'
    }
    today_key = datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d')
    attendance_records = conn.execute(
        'SELECT * FROM attendance WHERE date_key = ? ORDER BY login_at ASC', (today_key,)
    ).fetchall()
    attendance_count = len(attendance_records)

    # Configuration de la salle d'attente
    wr_enabled_row = conn.execute("SELECT value FROM settings WHERE key = 'waiting_room_enabled'").fetchone()
    waiting_room_config = {
        'enabled': wr_enabled_row['value'] if wr_enabled_row else '0'
    }
    wr_pending_row = conn.execute("SELECT COUNT(*) FROM waiting_room WHERE status = 'pending'").fetchone()
    waiting_room_pending_count = wr_pending_row[0] if wr_pending_row else 0

    conn.close()

    return render_template(
        'admin.html',
        medias=[dict(m) for m in medias],
        users=[dict(u) for u in users],
        invitations=[dict(i) for i in invitations],
        communiques=[dict(c) for c in communiques],
        series=[dict(s) for s in series],
        lives=[dict(l) for l in lives],
        radio_config=radio_config,
        attendance_config=attendance_config,
        attendance_records=[dict(r) for r in attendance_records],
        attendance_count=attendance_count,
        waiting_room_config=waiting_room_config,
        waiting_room_pending_count=waiting_room_pending_count,
        stats=stats,
        commissions_enseignement=COMMISSIONS_ENSEIGNEMENT,
        commissions_musique=COMMISSIONS_MUSIQUE,
        categories=CATEGORIES
    )


# ─────────────────────────────────────────────
# Routes Admin — Salle d'Attente
# ─────────────────────────────────────────────

@app.route('/admin/waiting-room/config', methods=['POST'])
@login_required
def admin_waiting_room_config():
    """Activer ou désactiver la salle d'attente."""
    enabled = request.form.get('waiting_room_enabled', '0').strip()
    conn = get_db()
    conn.execute("UPDATE settings SET value = ? WHERE key = 'waiting_room_enabled'", (enabled,))
    
    # Si on désactive la salle d'attente, on peut nettoyer la file
    if enabled == '0':
        conn.execute("DELETE FROM waiting_room")
        
    conn.commit()
    conn.close()
    flash("✅ Configuration de la salle d'attente mise à jour.", 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/waiting-room/records')
@login_required
def admin_waiting_room_records():
    """API admin pour récupérer la liste des entrées de la salle d'attente."""
    conn = get_db()
    records = conn.execute("SELECT * FROM waiting_room ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify({
        'records': [dict(r) for r in records]
    })


@app.route('/admin/waiting-room/action', methods=['POST'])
@login_required
def admin_waiting_room_action():
    """AJAX : Accepter ou rejeter une demande d'accès par e-mail."""
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    action = data.get('action', '').strip() # 'approve' or 'reject'
    
    if not email or action not in ['approve', 'reject']:
        return jsonify({'error': 'Paramètres invalides.'}), 400
        
    status = 'approved' if action == 'approve' else 'rejected'
    
    conn = get_db()
    conn.execute("UPDATE waiting_room SET status = ? WHERE email = ?", (status, email))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


@app.route('/admin/waiting-room/clear', methods=['POST'])
@login_required
def admin_waiting_room_clear():
    """Effacer toutes les demandes d'accès."""
    conn = get_db()
    conn.execute("DELETE FROM waiting_room")
    conn.commit()
    conn.close()
    flash('🗑️ Salle d\'attente réinitialisée.', 'success')
    return redirect(url_for('admin_dashboard'))


# ─────────────────────────────────────────────
# Routes Admin — Prise de Présence
# ─────────────────────────────────────────────

@app.route('/admin/attendance/config', methods=['POST'])
@login_required
def admin_attendance_config():
    """Sauvegarder la configuration de prise de présence."""
    enabled = request.form.get('attendance_enabled', '0')
    target_hour = request.form.get('attendance_target_hour', '09:00')
    conn = get_db()
    conn.execute("UPDATE settings SET value = ? WHERE key = 'attendance_enabled'", (enabled,))
    conn.execute("UPDATE settings SET value = ? WHERE key = 'attendance_target_hour'", (target_hour,))
    conn.commit()
    conn.close()
    flash('✅ Configuration de présence enregistrée.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/attendance/records')
@login_required
def admin_attendance_records():
    """Retourner les présences pour une date donnée (JSON)."""
    date_key = request.args.get('date', datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d'))
    conn = get_db()
    records = conn.execute(
        'SELECT * FROM attendance WHERE date_key = ? ORDER BY login_at ASC', (date_key,)
    ).fetchall()
    # Récupérer aussi la config pour le frontend
    att_hour_row = conn.execute("SELECT value FROM settings WHERE key = 'attendance_target_hour'").fetchone()
    target_hour = att_hour_row['value'] if att_hour_row else '09:00'
    conn.close()
    return jsonify({
        'records': [dict(r) for r in records],
        'target_hour': target_hour,
        'date': date_key,
        'count': len(records)
    })


@app.route('/admin/attendance/export-pdf')
@login_required
def admin_attendance_export_pdf():
    """Générer et télécharger un PDF des présences."""
    from fpdf import FPDF

    date_key = request.args.get('date', datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d'))
    conn = get_db()
    records = conn.execute(
        'SELECT * FROM attendance WHERE date_key = ? ORDER BY login_at ASC', (date_key,)
    ).fetchall()
    att_hour_row = conn.execute("SELECT value FROM settings WHERE key = 'attendance_target_hour'").fetchone()
    target_hour = att_hour_row['value'] if att_hour_row else '09:00'
    conn.close()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Titre
    pdf.set_font('Helvetica', 'B', 20)
    pdf.cell(0, 15, 'Rapport de Presence', new_x='LMARGIN', new_y='NEXT', align='C')
    pdf.set_font('Helvetica', '', 11)
    pdf.cell(0, 8, f'Ministere Apostolique Impact', new_x='LMARGIN', new_y='NEXT', align='C')
    pdf.ln(5)

    # Infos
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 7, f'Date : {date_key}', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, f'Heure cible : {target_hour}', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, f'Total presents : {len(records)}', new_x='LMARGIN', new_y='NEXT')
    on_time = len([r for r in records if not r['is_late']])
    late = len([r for r in records if r['is_late']])
    pdf.cell(0, 7, f'A l\'heure : {on_time}  |  En retard : {late}', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(8)

    # Tableau header
    pdf.set_fill_color(232, 130, 12)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(10, 10, '#', border=1, align='C', fill=True)
    pdf.cell(115, 10, 'Nom', border=1, fill=True)
    pdf.cell(35, 10, 'Heure arrivee', border=1, align='C', fill=True)
    pdf.cell(30, 10, 'Statut', border=1, align='C', fill=True)
    pdf.ln()

    # Tableau body
    pdf.set_font('Helvetica', '', 9)
    for i, rec in enumerate(records, 1):
        pdf.set_text_color(0, 0, 0)
        pdf.cell(10, 8, str(i), border=1, align='C')
        pdf.cell(115, 8, rec['display_name'], border=1)
        login_time = rec['login_at'].split(' ')[1] if ' ' in rec['login_at'] else rec['login_at']
        pdf.cell(35, 8, login_time, border=1, align='C')
        if rec['is_late']:
            pdf.set_text_color(220, 53, 69)
            pdf.cell(30, 8, 'En retard', border=1, align='C')
        else:
            pdf.set_text_color(76, 175, 80)
            pdf.cell(30, 8, 'A l\'heure', border=1, align='C')
        pdf.ln()

    # Footer
    pdf.ln(10)
    pdf.set_text_color(150, 150, 150)
    pdf.set_font('Helvetica', 'I', 8)
    pdf.cell(0, 7, f'Genere le {datetime.now(ATTENDANCE_TZ).strftime("%d/%m/%Y a %H:%M")} (heure de Bruxelles) - ImpactStream', align='C')

    # Output
    pdf_bytes = pdf.output()
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'presence_{date_key}.pdf'
    )


@app.route('/admin/attendance/clear', methods=['POST'])
@login_required
def admin_attendance_clear():
    """Effacer les présences d'une date donnée."""
    date_key = request.form.get('date', datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d'))
    conn = get_db()
    conn.execute('DELETE FROM attendance WHERE date_key = ?', (date_key,))
    conn.commit()
    conn.close()
    flash(f'🗑️ Présences du {date_key} effacées.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/attendance/evict', methods=['POST'])
@login_required
def admin_attendance_evict():
    """AJAX : Expulser un utilisateur connecté pour la journée."""
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    date_key = data.get('date', datetime.now(ATTENDANCE_TZ).strftime('%Y-%m-%d'))
    
    if not email:
        return jsonify({'error': 'Adresse e-mail manquante.'}), 400
        
    conn = get_db()
    conn.execute(
        'UPDATE attendance SET is_evicted = 1 WHERE user_email = ? AND date_key = ?',
        (email, date_key)
    )
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


@app.route('/admin/comptes/ajouter', methods=['POST'])
@login_required
def admin_add_user():
    """Ajouter un nouvel accès utilisateur."""
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()

    if not email or not password:
        flash('Veuillez remplir tous les champs.', 'error')
        return redirect(url_for('admin_dashboard', tab='comptes'))

    # Restriction de domaine
    if not email.endswith('@ministereimpact.org'):
        flash('Le domaine de l\'e-mail doit être @ministereimpact.org.', 'error')
        return redirect(url_for('admin_dashboard', tab='comptes'))

    # Hachage bcrypt/werkzeug
    hashed = generate_password_hash(password)

    conn = get_db()
    try:
        conn.execute('INSERT INTO users (email, password_hash, acces_nayoth, acces_intercession) VALUES (?, ?, 0, 0)', (email, hashed))
        conn.commit()
        flash(f'✅ Accès créé pour {email} !', 'success')
    except sqlite3.IntegrityError:
        flash('Cette adresse e-mail est déjà autorisée.', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard', tab='comptes'))

@app.route('/admin/comptes/toggle-acces', methods=['POST'])
@login_required
def admin_toggle_user_access():
    """Basculer l'accès Nayoth ou Intercession pour un membre via AJAX."""
    data = request.get_json() or {}
    user_id = data.get('user_id')
    access_type = data.get('type') # 'nayoth' or 'intercession'
    value = 1 if data.get('value') else 0

    if not user_id or access_type not in ['nayoth', 'intercession']:
        return jsonify({'error': 'Paramètres invalides.'}), 400

    conn = get_db()
    
    # Sécurité : Protéger le compte principal Yann
    user = conn.execute('SELECT email FROM users WHERE id = ?', (user_id,)).fetchone()
    if user and user['email'] == 'yann.noukaze@ministereimpact.org':
        conn.close()
        return jsonify({'error': 'Les accès du compte administrateur principal ne peuvent pas être modifiés.'}), 403

    column = 'acces_nayoth' if access_type == 'nayoth' else 'acces_intercession'
    conn.execute(f'UPDATE users SET {column} = ? WHERE id = ?', (value, user_id))
    conn.commit()
    conn.close()

    return jsonify({'success': True})

@app.route('/register', methods=['POST'])
def user_register():
    """Inscription publique sur invitation, restreinte au domaine ministereimpact.org."""
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    invite_token = request.form.get('invite_token', '').strip()

    if not invite_token:
        flash("L'inscription requiert un jeton d'invitation valide.", 'error')
        return redirect(url_for('user_login'))

    if not email or not password:
        flash('Veuillez remplir tous les champs.', 'error')
        return redirect(url_for('user_login', view='register', token=invite_token))

    # Étape 1 : Restriction stricte au domaine
    parts = email.split('@')
    if len(parts) != 2 or parts[1] != 'ministereimpact.org':
        flash('Domaine non autorisé', 'error')
        return redirect(url_for('user_login', view='register', token=invite_token))

    if len(password) < 6:
        flash('Le mot de passe doit faire au moins 6 caractères.', 'error')
        return redirect(url_for('user_login', view='register', token=invite_token))

    # Étape 2 : Vérification du jeton d'invitation
    conn = get_db()
    invite = conn.execute('SELECT * FROM invitations WHERE token = ? AND used = 0', (invite_token,)).fetchone()
    if not invite:
        conn.close()
        flash("Ce lien d'invitation est invalide ou a déjà été utilisé.", "error")
        return redirect(url_for('user_login'))

    if invite['email'] != email:
        conn.close()
        flash("Cette invitation est réservée à l'adresse e-mail associée.", "error")
        return redirect(url_for('user_login', view='register', token=invite_token))

    hashed = generate_password_hash(password)
    verification_token = secrets.token_urlsafe(16)

    try:
        # Création du compte utilisateur (inactif par défaut)
        conn.execute('''
            INSERT INTO users (email, password_hash, acces_nayoth, acces_intercession, is_verified, verification_token)
            VALUES (?, ?, 0, 0, 0, ?)
        ''', (email, hashed, verification_token))
        
        # Marquer l'invitation comme utilisée
        conn.execute('UPDATE invitations SET used = 1 WHERE id = ?', (invite['id'],))
        conn.commit()

        # Simulation d'envoi d'e-mail d'activation locale
        activation_link = f"{request.host_url.rstrip('/')}/verify-email?token={verification_token}"
        os.makedirs(os.path.join(BASE_DIR, 'static', 'emails'), exist_ok=True)
        email_file = os.path.join(BASE_DIR, 'static', 'emails', f"activation_{email}.txt")
        with open(email_file, 'w', encoding='utf-8') as f:
            f.write(f"Sujet: Activez votre compte ImpactStream\n\n"
                    f"Bonjour,\n\n"
                    f"Merci pour votre inscription à ImpactStream. Veuillez valider votre adresse e-mail et "
                    f"accéder au catalogue en cliquant sur le lien ci-dessous :\n\n"
                    f"{activation_link}\n\n"
                    f"L'équipe ImpactStream")

        flash(f"✅ Inscription réussie ! Un e-mail d'activation a été simulé. <a href='{activation_link}' style='text-decoration: underline; font-weight: bold;'>Cliquez ici pour l'activer et vous connecter</a>", 'success')
        return redirect(url_for('user_login'))
        
    except sqlite3.IntegrityError:
        flash('Cette adresse e-mail est déjà inscrite.', 'error')
        return redirect(url_for('user_login', view='register', token=invite_token))
    finally:
        conn.close()


@app.route('/verify-email')
def verify_email():
    """Valider l'adresse e-mail d'un nouvel inscrit."""
    token = request.args.get('token', '').strip()
    if not token:
        flash("Jeton de vérification manquant.", "error")
        return redirect(url_for('user_login'))

    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE verification_token = ?', (token,)).fetchone()

    if not user:
        conn.close()
        flash("Jeton de vérification invalide ou expiré.", "error")
        return redirect(url_for('user_login'))

    # Activer l'utilisateur
    conn.execute('UPDATE users SET is_verified = 1, verification_token = NULL WHERE id = ?', (user['id'],))
    conn.commit()
    conn.close()

    flash("✨ Votre adresse e-mail a été validée avec succès ! Vous pouvez maintenant vous connecter.", "success")
    return redirect(url_for('user_login'))


@app.route('/admin/invitation/generer', methods=['POST'])
@login_required
def admin_generate_invitation():
    """Générer un lien d'invitation unique pour un e-mail."""
    email = request.form.get('email', '').strip().lower()
    if not email:
        flash("L'adresse e-mail est obligatoire.", "error")
        return redirect(url_for('admin_dashboard'))

    # Restriction stricte au domaine
    parts = email.split('@')
    if len(parts) != 2 or parts[1] != 'ministereimpact.org':
        flash("L'e-mail doit appartenir au domaine @ministereimpact.org.", "error")
        return redirect(url_for('admin_dashboard'))

    token = secrets.token_urlsafe(16)
    conn = get_db()
    
    # Vérifier si cet e-mail est déjà un utilisateur inscrit
    existing_user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    if existing_user:
        conn.close()
        flash(f"⚠️ Un compte existe déjà pour l'adresse {email}.", "error")
        return redirect(url_for('admin_dashboard'))

    try:
        conn.execute('INSERT INTO invitations (email, token) VALUES (?, ?)', (email, token))
        conn.commit()
        flash(f"✅ Invitation générée avec succès pour {email} !", "success")
    except sqlite3.IntegrityError:
        # Renouveler l'invitation existante non consommée
        token = secrets.token_urlsafe(16)
        conn.execute('UPDATE invitations SET token = ?, used = 0 WHERE email = ?', (token, email))
        conn.commit()
        flash(f"🔄 Invitation précédente renouvelée avec succès pour {email} !", "success")
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/invitation/supprimer/<int:invite_id>', methods=['POST'])
@login_required
def admin_delete_invitation(invite_id):
    """Supprimer une invitation existante."""
    conn = get_db()
    conn.execute('DELETE FROM invitations WHERE id = ?', (invite_id,))
    conn.commit()
    conn.close()
    flash("🗑️ Invitation supprimée avec succès !", "success")
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/communiques/ajouter', methods=['POST'])
@login_required
def admin_add_communique():
    """Ajouter un communiqué."""
    titre = request.form.get('titre', '').strip()
    contenu_fr = request.form.get('contenu_fr', '').strip()
    contenu_en = request.form.get('contenu_en', '').strip() or None
    contenu_es = request.form.get('contenu_es', '').strip() or None
    contenu_nl = request.form.get('contenu_nl', '').strip() or None
    contenu_ln = request.form.get('contenu_ln', '').strip() or None
    date_creation = request.form.get('date_creation', '').strip() or None

    if not titre or not contenu_fr:
        flash("Le titre et le contenu en français sont obligatoires.", "error")
        return redirect(url_for('admin_dashboard'))

    conn = get_db()
    if date_creation:
        conn.execute('''
            INSERT INTO communiques (titre, contenu_fr, contenu_en, contenu_es, contenu_nl, contenu_ln, date_creation)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (titre, contenu_fr, contenu_en, contenu_es, contenu_nl, contenu_ln, date_creation))
    else:
        conn.execute('''
            INSERT INTO communiques (titre, contenu_fr, contenu_en, contenu_es, contenu_nl, contenu_ln)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (titre, contenu_fr, contenu_en, contenu_es, contenu_nl, contenu_ln))
    conn.commit()
    conn.close()

    flash("📢 Communiqué publié avec succès !", "success")
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/communiques/editer/<int:comm_id>', methods=['POST'])
@login_required
def admin_edit_communique(comm_id):
    """Modifier un communiqué existant."""
    titre = request.form.get('titre', '').strip()
    contenu_fr = request.form.get('contenu_fr', '').strip()
    contenu_en = request.form.get('contenu_en', '').strip() or None
    contenu_es = request.form.get('contenu_es', '').strip() or None
    contenu_nl = request.form.get('contenu_nl', '').strip() or None
    contenu_ln = request.form.get('contenu_ln', '').strip() or None
    date_creation = request.form.get('date_creation', '').strip() or None

    if not titre or not contenu_fr:
        flash("Le titre et le contenu en français sont obligatoires.", "error")
        return redirect(url_for('admin_dashboard'))

    conn = get_db()
    if date_creation:
        conn.execute('''
            UPDATE communiques
            SET titre = ?, contenu_fr = ?, contenu_en = ?, contenu_es = ?, contenu_nl = ?, contenu_ln = ?, date_creation = ?
            WHERE id = ?
        ''', (titre, contenu_fr, contenu_en, contenu_es, contenu_nl, contenu_ln, date_creation, comm_id))
    else:
        conn.execute('''
            UPDATE communiques
            SET titre = ?, contenu_fr = ?, contenu_en = ?, contenu_es = ?, contenu_nl = ?, contenu_ln = ?
            WHERE id = ?
        ''', (titre, contenu_fr, contenu_en, contenu_es, contenu_nl, contenu_ln, comm_id))
    conn.commit()
    conn.close()

    flash("🔄 Communiqué mis à jour avec succès !", "success")
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/communiques/supprimer/<int:comm_id>', methods=['POST'])
@login_required
def admin_delete_communique(comm_id):
    """Supprimer un communiqué."""
    conn = get_db()
    conn.execute('DELETE FROM communiques WHERE id = ?', (comm_id,))
    conn.commit()
    conn.close()
    flash("🗑️ Communiqué supprimé avec succès !", "success")
    return redirect(url_for('admin_dashboard'))


@app.route('/api/communiques')
@user_login_required
def api_list_communiques():
    """API pour lister les communiqués."""
    conn = get_db()
    communiques = conn.execute('SELECT * FROM communiques ORDER BY id DESC').fetchall()
    conn.close()
    return jsonify([dict(c) for c in communiques])


@app.route('/admin/comptes/supprimer/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    """Supprimer un accès utilisateur."""
    conn = get_db()
    
    # Sécurité : Vérifier le nombre d'utilisateurs restants
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if count <= 1:
        flash('Sécurité : Impossible de supprimer le dernier accès restant.', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard', tab='comptes'))

    user = conn.execute('SELECT email FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        flash(f'🗑️ Accès révoqué pour {user["email"]}.', 'success')
    else:
        flash('Compte introuvable.', 'error')
    conn.close()

    return redirect(url_for('admin_dashboard', tab='comptes'))


@app.route('/admin/ajouter', methods=['POST'])
@login_required
def admin_add_media():
    """Ajouter un nouveau média."""
    titre = request.form.get('titre', '').strip()
    description = request.form.get('description', '').strip()
    categorie = request.form.get('categorie', '').strip()
    commission = request.form.get('commission', '').strip()
    langue = request.form.get('langue', '').strip()

    if not titre or not categorie or (categorie in ('Enseignement', 'Musique') and not commission):
        flash('Le titre, la catégorie et la sous-section sont obligatoires.', 'error')
        return redirect(url_for('admin_dashboard'))

    if categorie not in ('Enseignement', 'Musique'):
        commission = ''

    # Vérifier s'il s'agit d'un lien externe ou d'un fichier local
    media_url = request.form.get('media_url', '').strip()
    media_url_en = request.form.get('media_url_en', '').strip()
    media_url_es = request.form.get('media_url_es', '').strip()
    media_url_nl = request.form.get('media_url_nl', '').strip()
    media_url_ln = request.form.get('media_url_ln', '').strip()
    paroles_keywords = request.form.get('paroles_keywords', '').strip()
    position_banniere = request.form.get('position_banniere', '15').strip()
    try:
        position_banniere = int(position_banniere)
    except ValueError:
        position_banniere = 15

    series_id = request.form.get('series_id', '').strip() or None
    if series_id:
        try:
            series_id = int(series_id)
        except ValueError:
            series_id = None

    chapitre = request.form.get('chapitre', '').strip() or None
    ordre_episode = request.form.get('ordre_episode', '').strip() or None
    if ordre_episode:
        try:
            ordre_episode = int(ordre_episode)
        except ValueError:
            ordre_episode = None

    url_video = None

    if media_url:
        url_video = media_url
    else:
        media_file = request.files.get('media_file')
        if media_file and media_file.filename:
            allowed = ALLOWED_VIDEO | ALLOWED_AUDIO
            url_video = save_upload(media_file, UPLOAD_VIDEOS, allowed)
            if not url_video:
                flash('Format de fichier média non supporté.', 'error')
                return redirect(url_for('admin_dashboard'))

    if not url_video:
        flash('Veuillez fournir un fichier média ou un lien externe pour le français (par défaut).', 'error')
        return redirect(url_for('admin_dashboard'))

    # Upload de la miniature
    thumbnail_file = request.files.get('thumbnail_file')
    url_miniature = None
    if thumbnail_file and thumbnail_file.filename:
        url_miniature = save_upload(thumbnail_file, UPLOAD_IMAGES, ALLOWED_IMAGE)

    # Insérer en base
    conn = get_db()
    conn.execute('''
        INSERT INTO medias (titre, description, categorie, commission, langue, url_miniature, url_video, url_video_en, url_video_es, url_video_nl, url_video_ln, paroles_keywords, position_banniere, series_id, chapitre, ordre_episode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (titre, description, categorie, commission, langue, url_miniature, url_video, media_url_en, media_url_es, media_url_nl, media_url_ln, paroles_keywords, position_banniere, series_id, chapitre, ordre_episode))
    conn.commit()
    conn.close()

    flash(f'✅ « {titre} » a été ajouté avec succès !', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/modifier/<int:media_id>', methods=['POST'])
@login_required
def admin_edit_media(media_id):
    """Modifier un média existant."""
    conn = get_db()
    media = conn.execute('SELECT * FROM medias WHERE id = ?', (media_id,)).fetchone()

    if not media:
        flash('Média introuvable.', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    titre = request.form.get('titre', '').strip()
    description = request.form.get('description', '').strip()
    categorie = request.form.get('categorie', '').strip()
    commission = request.form.get('commission', '').strip()
    langue = request.form.get('langue', '').strip()
    if not titre or not categorie or (categorie in ('Enseignement', 'Musique') and not commission):
        flash('Le titre, la catégorie et la sous-section sont obligatoires.', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    if categorie not in ('Enseignement', 'Musique'):
        commission = ''
    # Récupérer le mode de source et les autres langues / paroles
    source_type = request.form.get('source_type', 'local')
    media_url = request.form.get('media_url', '').strip()
    media_url_en = request.form.get('media_url_en', '').strip()
    media_url_es = request.form.get('media_url_es', '').strip()
    media_url_nl = request.form.get('media_url_nl', '').strip()
    media_url_ln = request.form.get('media_url_ln', '').strip()
    paroles_keywords = request.form.get('paroles_keywords', '').strip()
    position_banniere = request.form.get('position_banniere', '15').strip()
    try:
        position_banniere = int(position_banniere)
    except ValueError:
        position_banniere = 15

    series_id = request.form.get('series_id', '').strip() or None
    if series_id:
        try:
            series_id = int(series_id)
        except ValueError:
            series_id = None

    chapitre = request.form.get('chapitre', '').strip() or None
    ordre_episode = request.form.get('ordre_episode', '').strip() or None
    if ordre_episode:
        try:
            ordre_episode = int(ordre_episode)
        except ValueError:
            ordre_episode = None

    url_video = media['url_video']

    if source_type == 'external':
        url_video = media_url
    else:
        media_file = request.files.get('media_file')
        if media_file and media_file.filename:
            allowed = ALLOWED_VIDEO | ALLOWED_AUDIO
            new_url = save_upload(media_file, UPLOAD_VIDEOS, allowed)
            if new_url:
                # Supprimer l'ancien fichier local physique
                if media['url_video'] and media['url_video'].startswith('/static/'):
                    old_path = os.path.join(BASE_DIR, media['url_video'].lstrip('/'))
                    if os.path.exists(old_path):
                        try:
                            os.remove(old_path)
                        except Exception:
                            pass
                url_video = new_url

    # Upload d'une nouvelle miniature (optionnel)
    thumbnail_file = request.files.get('thumbnail_file')
    url_miniature = media['url_miniature']
    if thumbnail_file and thumbnail_file.filename:
        new_thumb = save_upload(thumbnail_file, UPLOAD_IMAGES, ALLOWED_IMAGE)
        if new_thumb:
            if url_miniature:
                old_path = os.path.join(BASE_DIR, url_miniature.lstrip('/'))
                if os.path.exists(old_path):
                    os.remove(old_path)
            url_miniature = new_thumb

    conn.execute('''
        UPDATE medias 
        SET titre=?, description=?, categorie=?, commission=?, langue=?, url_miniature=?, url_video=?,
            url_video_en=?, url_video_es=?, url_video_nl=?, url_video_ln=?, paroles_keywords=?, position_banniere=?,
            series_id=?, chapitre=?, ordre_episode=?
        WHERE id=?
    ''', (titre, description, categorie, commission, langue, url_miniature, url_video,
          media_url_en, media_url_es, media_url_nl, media_url_ln, paroles_keywords, position_banniere,
          series_id, chapitre, ordre_episode, media_id))
    conn.commit()
    conn.close()

    flash(f'✅ « {titre} » a été modifié avec succès !', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/supprimer/<int:media_id>', methods=['POST'])
@login_required
def admin_delete_media(media_id):
    """Supprimer un média."""
    conn = get_db()
    media = conn.execute('SELECT * FROM medias WHERE id = ?', (media_id,)).fetchone()

    if media:
        # Supprimer les fichiers associés
        if media['url_video']:
            filepath = os.path.join(BASE_DIR, media['url_video'].lstrip('/'))
            if os.path.exists(filepath):
                os.remove(filepath)

        if media['url_miniature']:
            filepath = os.path.join(BASE_DIR, media['url_miniature'].lstrip('/'))
            if os.path.exists(filepath):
                os.remove(filepath)

        conn.execute('DELETE FROM medias WHERE id = ?', (media_id,))
        conn.commit()
        flash(f'🗑️ « {media["titre"]} » a été supprimé.', 'success')
    else:
        flash('Média introuvable.', 'error')

    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/api/media/<int:media_id>')
@login_required
def admin_api_media(media_id):
    """API admin pour récupérer les détails d'un média (pour l'édition)."""
    conn = get_db()
    media = conn.execute('SELECT * FROM medias WHERE id = ?', (media_id,)).fetchone()
    conn.close()
    if media:
        return jsonify(dict(media))
    return jsonify({'error': 'Non trouvé'}), 404


# ─────────────────────────────────────────────
# Routes Admin / API - Séries
# ─────────────────────────────────────────────

@app.route('/admin/series/ajouter', methods=['POST'])
@login_required
def admin_add_series():
    """Ajouter une nouvelle série thématique."""
    titre = request.form.get('titre', '').strip()
    description = request.form.get('description', '').strip()
    categorie = request.form.get('categorie', '').strip()
    commission = request.form.get('commission', '').strip()

    if not titre or not categorie:
        flash("Le titre et la catégorie sont obligatoires.", "error")
        return redirect(url_for('admin_dashboard'))

    # Upload miniature de la série
    thumbnail_file = request.files.get('thumbnail_file')
    url_miniature = None
    if thumbnail_file and thumbnail_file.filename:
        url_miniature = save_upload(thumbnail_file, UPLOAD_IMAGES, ALLOWED_IMAGE)

    conn = get_db()
    conn.execute('''
        INSERT INTO series (titre, description, categorie, commission, url_miniature)
        VALUES (?, ?, ?, ?, ?)
    ''', (titre, description, categorie, commission, url_miniature))
    conn.commit()
    conn.close()

    flash("📚 Série thématique créée avec succès !", "success")
    return redirect(url_for('admin_dashboard', tab='series'))


@app.route('/admin/series/editer/<int:series_id>', methods=['POST'])
@login_required
def admin_edit_series(series_id):
    """Modifier une série existante."""
    conn = get_db()
    series = conn.execute('SELECT * FROM series WHERE id = ?', (series_id,)).fetchone()
    if not series:
        conn.close()
        flash("Série introuvable.", "error")
        return redirect(url_for('admin_dashboard'))

    titre = request.form.get('titre', '').strip()
    description = request.form.get('description', '').strip()
    categorie = request.form.get('categorie', '').strip()
    commission = request.form.get('commission', '').strip()

    if not titre or not categorie:
        conn.close()
        flash("Le titre et la catégorie sont obligatoires.", "error")
        return redirect(url_for('admin_dashboard'))

    thumbnail_file = request.files.get('thumbnail_file')
    url_miniature = series['url_miniature']
    if thumbnail_file and thumbnail_file.filename:
        new_thumb = save_upload(thumbnail_file, UPLOAD_IMAGES, ALLOWED_IMAGE)
        if new_thumb:
            # Delete old image if exists
            if url_miniature:
                old_path = os.path.join(BASE_DIR, url_miniature.lstrip('/'))
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass
            url_miniature = new_thumb

    conn.execute('''
        UPDATE series
        SET titre = ?, description = ?, categorie = ?, commission = ?, url_miniature = ?
        WHERE id = ?
    ''', (titre, description, categorie, commission, url_miniature, series_id))
    conn.commit()
    conn.close()

    flash("📚 Série thématique mise à jour avec succès !", "success")
    return redirect(url_for('admin_dashboard', tab='series'))


@app.route('/admin/series/supprimer/<int:series_id>', methods=['POST'])
@login_required
def admin_delete_series(series_id):
    """Supprimer une série et dissocier les médias associés."""
    conn = get_db()
    series = conn.execute('SELECT * FROM series WHERE id = ?', (series_id,)).fetchone()
    if series:
        # Delete thumbnail file
        if series['url_miniature']:
            filepath = os.path.join(BASE_DIR, series['url_miniature'].lstrip('/'))
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

        # Dissocier les médias liés
        conn.execute('UPDATE medias SET series_id = NULL, chapitre = NULL, ordre_episode = NULL WHERE series_id = ?', (series_id,))
        conn.execute('DELETE FROM series WHERE id = ?', (series_id,))
        conn.commit()
        flash("🗑️ Série supprimée avec succès (les médias associés ont été détachés).", "success")
    else:
        flash("Série introuvable.", "error")

    conn.close()
    return redirect(url_for('admin_dashboard', tab='series'))


@app.route('/admin/api/series')
@login_required
def admin_api_list_series():
    """API admin pour lister toutes les séries."""
    conn = get_db()
    rows = conn.execute('SELECT * FROM series ORDER BY titre').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/admin/api/series/<int:series_id>')
@login_required
def admin_api_series_detail(series_id):
    """API admin pour obtenir le détail d'une série."""
    conn = get_db()
    series = conn.execute('SELECT * FROM series WHERE id = ?', (series_id,)).fetchone()
    conn.close()
    if series:
        return jsonify(dict(series))
    return jsonify({'error': 'Non trouvé'}), 404


@app.route('/api/series/<int:series_id>')
@user_login_required
def api_get_series(series_id):
    """API publique pour obtenir les détails d'une série par son ID."""
    conn = get_db()
    s = conn.execute('SELECT * FROM series WHERE id = ?', (series_id,)).fetchone()
    conn.close()
    if s:
        return jsonify(dict(s))
    return jsonify({'error': 'Série non trouvée'}), 404


@app.route('/api/series')
@user_login_required
def api_list_series():
    """API publique pour lister les séries avec filtrage d'accès et de filtres."""
    categorie = request.args.get('categorie', '').strip()
    commission = request.args.get('commission', '').strip()

    conn = get_db()
    # Récupérer l'accès de l'utilisateur
    user_email = session.get('user_email')
    user_row = conn.execute('SELECT acces_nayoth, acces_intercession FROM users WHERE email = ?', (user_email,)).fetchone()
    has_nayoth = user_row['acces_nayoth'] if user_row else 0
    has_intercession = user_row['acces_intercession'] if user_row else 0

    query = 'SELECT * FROM series WHERE 1=1'
    params = []

    # Filtrage de sécurité
    if not has_nayoth:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Nayoth')"
    if not has_intercession:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Intercession')"

    # Exclure FGI et Enfants du carrousel de séries par défaut, sauf si demandé explicitement
    if commission != 'FGI':
        query += " AND (commission IS NULL OR commission != 'FGI')"
    if commission != 'Enfants':
        query += " AND (commission IS NULL OR commission != 'Enfants')"

    if categorie:
        query += " AND categorie = ?"
        params.append(categorie)
    if commission:
        query += " AND commission = ?"
        params.append(commission)

    query += " ORDER BY id DESC"
    series = conn.execute(query, params).fetchall()
    
    # Compter les épisodes pour chaque série
    res = []
    for s in series:
        s_dict = dict(s)
        # Compter le nombre de médias associés autorisés pour l'utilisateur
        count_query = 'SELECT COUNT(*) FROM medias WHERE series_id = ?'
        count_params = [s['id']]
        if not has_nayoth:
            count_query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Nayoth')"
        if not has_intercession:
            count_query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Intercession')"
        
        episodes_count = conn.execute(count_query, count_params).fetchone()[0]
        s_dict['nb_episodes'] = episodes_count
        res.append(s_dict)

    conn.close()
    return jsonify(res)


@app.route('/api/series/<int:series_id>/medias')
@user_login_required
def api_series_medias(series_id):
    """API publique pour lister les épisodes autorisés d'une série triés par chapitre et ordre."""
    conn = get_db()
    series = conn.execute('SELECT * FROM series WHERE id = ?', (series_id,)).fetchone()
    if not series:
        conn.close()
        return jsonify({'error': 'Série introuvable'}), 404

    # Sécurité
    user_email = session.get('user_email')
    user_row = conn.execute('SELECT acces_nayoth, acces_intercession FROM users WHERE email = ?', (user_email,)).fetchone()
    has_nayoth = user_row['acces_nayoth'] if user_row else 0
    has_intercession = user_row['acces_intercession'] if user_row else 0

    if series['categorie'] == 'Enseignement':
        if series['commission'] == 'Nayoth' and not has_nayoth:
            conn.close()
            return jsonify({'error': 'Accès interdit'}), 403
        if series['commission'] == 'Intercession' and not has_intercession:
            conn.close()
            return jsonify({'error': 'Accès interdit'}), 403

    query = '''
        SELECT * FROM medias 
        WHERE series_id = ? 
    '''
    params = [series_id]
    
    if not has_nayoth:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Nayoth')"
    if not has_intercession:
        query += " AND (categorie != 'Enseignement' OR commission IS NULL OR commission != 'Intercession')"

    # Tri par chapitre (lexicographique) et par ordre d'épisode
    query += " ORDER BY chapitre ASC, ordre_episode ASC, id ASC"
    
    medias = conn.execute(query, params).fetchall()
    conn.close()

    return jsonify([dict(m) for m in medias])


@app.route('/api/radio', methods=['GET'])
def public_api_radio():
    """Récupérer le statut de la radio et l'URL du flux."""
    conn = get_db()
    radio_enabled = conn.execute("SELECT value FROM settings WHERE key = 'radio_enabled'").fetchone()
    radio_stream_url = conn.execute("SELECT value FROM settings WHERE key = 'radio_stream_url'").fetchone()
    conn.close()
    
    return jsonify({
        'radio_enabled': radio_enabled['value'] == '1' if radio_enabled else False,
        'radio_stream_url': radio_stream_url['value'] if radio_stream_url else ''
    })


@app.route('/admin/radio/config', methods=['POST'])
@login_required
def admin_save_radio_config():
    """Enregistrer les configurations de la radio en direct."""
    radio_enabled = request.form.get('radio_enabled', '0').strip()
    radio_stream_url = request.form.get('radio_stream_url', '').strip()
    
    if not radio_stream_url:
        flash("L'URL du flux radio est obligatoire.", "error")
        return redirect(url_for('admin_dashboard', tab='radio'))
    
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('radio_enabled', ?)", (radio_enabled,))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('radio_stream_url', ?)", (radio_stream_url,))
    conn.commit()
    conn.close()
    
    flash("✅ La configuration de la radio a été mise à jour.", "success")
    return redirect(url_for('admin_dashboard', tab='radio'))


@app.route('/api/lives', methods=['GET'])
def public_api_lives():
    """Récupérer tous les directs vidéo actifs."""
    conn = get_db()
    lives = conn.execute("SELECT * FROM live_streams WHERE is_active = 1 ORDER BY id DESC").fetchall()
    conn.close()
    resp = jsonify([dict(l) for l in lives])
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


@app.route('/admin/lives/ajouter', methods=['POST'])
@login_required
def admin_add_live():
    """Ajouter un nouveau direct vidéo."""
    titre = request.form.get('titre', '').strip()
    description = request.form.get('description', '').strip()
    url_direct = request.form.get('url_direct', '').strip()
    is_active = int(request.form.get('is_active', '0'))
    
    if not titre or not url_direct:
        flash("Le titre et l'URL du direct sont obligatoires.", "error")
        return redirect(url_for('admin_dashboard', tab='lives'))
    
    conn = get_db()
    conn.execute(
        "INSERT INTO live_streams (titre, description, url_direct, is_active) VALUES (?, ?, ?, ?)",
        (titre, description, url_direct, is_active)
    )
    conn.commit()
    conn.close()
    flash("✅ Direct vidéo ajouté avec succès.", "success")
    return redirect(url_for('admin_dashboard', tab='lives'))


@app.route('/admin/lives/modifier/<int:live_id>', methods=['POST'])
@login_required
def admin_edit_live(live_id):
    """Modifier un direct vidéo existant."""
    titre = request.form.get('titre', '').strip()
    description = request.form.get('description', '').strip()
    url_direct = request.form.get('url_direct', '').strip()
    is_active = int(request.form.get('is_active', '0'))
    
    if not titre or not url_direct:
        flash("Le titre et l'URL du direct sont obligatoires.", "error")
        return redirect(url_for('admin_dashboard', tab='lives'))
    
    conn = get_db()
    conn.execute(
        "UPDATE live_streams SET titre = ?, description = ?, url_direct = ?, is_active = ? WHERE id = ?",
        (titre, description, url_direct, is_active, live_id)
    )
    conn.commit()
    conn.close()
    flash("✅ Direct vidéo modifié avec succès.", "success")
    return redirect(url_for('admin_dashboard', tab='lives'))


@app.route('/admin/lives/supprimer/<int:live_id>', methods=['POST'])
@login_required
def admin_delete_live(live_id):
    """Supprimer un direct vidéo."""
    conn = get_db()
    conn.execute("DELETE FROM live_streams WHERE id = ?", (live_id,))
    conn.commit()
    conn.close()
    flash("✅ Direct vidéo supprimé.", "success")
    return redirect(url_for('admin_dashboard', tab='lives'))


# ─────────────────────────────────────────────
# Quiz Routes hmm
# ─────────────────────────────────────────────

@app.route('/api/quizzes', methods=['GET'])
@user_login_required
def api_list_quizzes():
    """Liste tous les quiz actifs avec le nombre de questions de chacun."""
    conn = get_db()
    quizzes = conn.execute('''
        SELECT q.id, q.titre, q.description, COUNT(qq.id) as nb_questions
        FROM quizzes q
        LEFT JOIN quiz_questions qq ON q.id = qq.quiz_id
        GROUP BY q.id
        ORDER BY q.id DESC
    ''').fetchall()
    conn.close()
    return jsonify([dict(row) for row in quizzes])

@app.route('/api/quizzes/<int:quiz_id>/questions', methods=['GET'])
@user_login_required
def api_get_quiz_questions(quiz_id):
    """Récupère toutes les questions d'un quiz spécifique."""
    conn = get_db()
    questions = conn.execute('''
        SELECT id, question_text, option_a, option_b, option_c, correct_option
        FROM quiz_questions
        WHERE quiz_id = ?
        ORDER BY id ASC
    ''', (quiz_id,)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in questions])

@app.route('/admin/api/quizzes', methods=['GET'])
@login_required
def admin_api_list_quizzes():
    """Liste tous les quiz pour le panneau d'admin."""
    conn = get_db()
    quizzes = conn.execute('''
        SELECT q.id, q.titre, q.description, COUNT(qq.id) as nb_questions
        FROM quizzes q
        LEFT JOIN quiz_questions qq ON q.id = qq.quiz_id
        GROUP BY q.id
        ORDER BY q.id DESC
    ''').fetchall()
    conn.close()
    return jsonify([dict(row) for row in quizzes])

@app.route('/admin/api/quizzes', methods=['POST'])
@login_required
def admin_api_create_quiz():
    """Ajouter un nouveau quiz."""
    data = request.get_json() or {}
    titre = data.get('titre', '').strip()
    description = data.get('description', '').strip()
    if not titre:
        return jsonify({'error': 'Le titre est obligatoire'}), 400
    
    conn = get_db()
    cursor = conn.execute(
        'INSERT INTO quizzes (titre, description) VALUES (?, ?)',
        (titre, description)
    )
    quiz_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'quiz_id': quiz_id})

@app.route('/admin/api/quizzes/<int:quiz_id>', methods=['PUT'])
@login_required
def admin_api_update_quiz(quiz_id):
    """Modifier un quiz (titre et description)."""
    data = request.get_json() or {}
    titre = data.get('titre', '').strip()
    description = data.get('description', '').strip()
    if not titre:
        return jsonify({'error': 'Le titre est obligatoire'}), 400
    
    conn = get_db()
    conn.execute(
        'UPDATE quizzes SET titre = ?, description = ? WHERE id = ?',
        (titre, description, quiz_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/api/quizzes/<int:quiz_id>', methods=['DELETE'])
@login_required
def admin_api_delete_quiz(quiz_id):
    """Supprimer un quiz et toutes ses questions associés."""
    conn = get_db()
    conn.execute('DELETE FROM quiz_questions WHERE quiz_id = ?', (quiz_id,))
    conn.execute('DELETE FROM quizzes WHERE id = ?', (quiz_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/api/quizzes/<int:quiz_id>/questions', methods=['GET'])
@login_required
def admin_api_get_questions(quiz_id):
    """Récupérer les questions pour l'éditeur."""
    conn = get_db()
    questions = conn.execute(
        'SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY id ASC',
        (quiz_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in questions])

@app.route('/admin/api/quizzes/<int:quiz_id>/questions', methods=['POST'])
@login_required
def admin_api_save_questions(quiz_id):
    """Enregistrer toutes les questions d'un quiz (remplace les anciennes)."""
    data = request.get_json() or []
    conn = get_db()
    try:
        conn.execute('DELETE FROM quiz_questions WHERE quiz_id = ?', (quiz_id,))
        for q in data:
            question_text = q.get('question_text', '').strip()
            option_a = q.get('option_a', '').strip()
            option_b = q.get('option_b', '').strip()
            option_c = q.get('option_c', '').strip()
            correct_option = int(q.get('correct_option', 0))
            if question_text and option_a and option_b and option_c:
                conn.execute('''
                    INSERT INTO quiz_questions (quiz_id, question_text, option_a, option_b, option_c, correct_option)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (quiz_id, question_text, option_a, option_b, option_c, correct_option))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ─────────────────────────────────────────────
# PWA Routes
# ─────────────────────────────────────────────

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def serve_sw():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


# ─────────────────────────────────────────────
# Lancement
# ─────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    print("\n🎬 ImpactStream — Ministère Apostolique Impact")
    print("📍 Site public  : http://127.0.0.1:5001")
    print("🔐 Espace admin : http://127.0.0.1:5001/admin/login\n")
    app.run(debug=True, host='0.0.0.0', port=5001)
