import re

with open('app.py', 'r') as f:
    content = f.read()

injection = """
                # Full auto-migration pour les tables principales manquantes
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS medias (
                        id SERIAL PRIMARY KEY,
                        titre VARCHAR(255) NOT NULL,
                        description TEXT,
                        categorie VARCHAR(100),
                        commission VARCHAR(100),
                        langue VARCHAR(50),
                        url_miniature VARCHAR(500),
                        url_video VARCHAR(500),
                        url_video_en VARCHAR(500),
                        url_video_es VARCHAR(500),
                        url_video_nl VARCHAR(500),
                        url_video_ln VARCHAR(500),
                        paroles_keywords TEXT,
                        position_banniere INTEGER DEFAULT 15,
                        series_id INTEGER,
                        chapitre VARCHAR(100),
                        ordre_episode INTEGER,
                        date_ajout TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        acces_nayoth INTEGER DEFAULT 0,
                        acces_intercession INTEGER DEFAULT 0,
                        is_verified INTEGER DEFAULT 0,
                        verification_token VARCHAR(255),
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS invitations (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        token VARCHAR(255) UNIQUE NOT NULL,
                        used INTEGER DEFAULT 0,
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS communiques (
                        id SERIAL PRIMARY KEY,
                        titre VARCHAR(255) NOT NULL,
                        contenu_fr TEXT NOT NULL,
                        contenu_en TEXT,
                        contenu_es TEXT,
                        contenu_nl TEXT,
                        contenu_ln TEXT,
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS series (
                        id SERIAL PRIMARY KEY,
                        titre VARCHAR(255) NOT NULL,
                        description TEXT,
                        categorie VARCHAR(100) NOT NULL,
                        commission VARCHAR(100),
                        url_miniature VARCHAR(500),
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS live_streams (
                        id SERIAL PRIMARY KEY,
                        titre VARCHAR(255) NOT NULL,
                        description TEXT,
                        url_direct VARCHAR(500),
                        type_diffusion VARCHAR(50) DEFAULT 'standard',
                        is_active INTEGER DEFAULT 0,
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS quizzes (
                        id SERIAL PRIMARY KEY,
                        titre VARCHAR(255) NOT NULL,
                        description TEXT,
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS quiz_questions (
                        id SERIAL PRIMARY KEY,
                        quiz_id INTEGER REFERENCES quizzes(id) ON DELETE CASCADE,
                        question_text TEXT NOT NULL,
                        option_a VARCHAR(255) NOT NULL,
                        option_b VARCHAR(255) NOT NULL,
                        option_c VARCHAR(255) NOT NULL,
                        correct_option INTEGER NOT NULL
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS commentaires (
                        id SERIAL PRIMARY KEY,
                        media_id INTEGER,
                        user_email VARCHAR(255) NOT NULL,
                        nom_complet VARCHAR(255) NOT NULL,
                        contenu TEXT NOT NULL,
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
"""

target = "CREATE TABLE IF NOT EXISTS settings ("
replacement = injection + "\n                # Création table settings pour PostgreSQL\n                cur.execute('''\n                    " + target

content = content.replace("                # Création table settings pour PostgreSQL\n                cur.execute('''\n                    CREATE TABLE IF NOT EXISTS settings (", replacement)

# Add radio_enabled, radio_stream_url, podcast_enabled, attendance_enabled, attendance_target_hour to the postgres inserts
target_inserts = "cur.execute(\"INSERT INTO settings (key, value) VALUES ('meet_enabled', '0') ON CONFLICT (key) DO NOTHING\")"
inserts_replacement = target_inserts + """
                cur.execute("INSERT INTO settings (key, value) VALUES ('radio_enabled', '0') ON CONFLICT (key) DO NOTHING")
                cur.execute("INSERT INTO settings (key, value) VALUES ('radio_stream_url', 'https://icecast.radiofrance.fr/fip-midfi.mp3') ON CONFLICT (key) DO NOTHING")
                cur.execute("INSERT INTO settings (key, value) VALUES ('podcast_enabled', '1') ON CONFLICT (key) DO NOTHING")
                cur.execute("INSERT INTO settings (key, value) VALUES ('attendance_enabled', '0') ON CONFLICT (key) DO NOTHING")
                cur.execute("INSERT INTO settings (key, value) VALUES ('attendance_target_hour', '09:00') ON CONFLICT (key) DO NOTHING")
"""
content = content.replace(target_inserts, inserts_replacement)


with open('app.py', 'w') as f:
    f.write(content)
print("app.py patched!")
