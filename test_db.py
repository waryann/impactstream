import psycopg2

conn = psycopg2.connect('postgresql://postgres:ALjoFYRFOrFHehlP@db.vpbswejikerxfbebxuql.supabase.co:5432/postgres')
cur = conn.cursor()

queries = [
    '''CREATE TABLE IF NOT EXISTS waiting_room (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''',
    '''CREATE TABLE IF NOT EXISTS settings (
                        key VARCHAR(100) PRIMARY KEY,
                        value TEXT
                    )''',
    "INSERT INTO settings (key, value) VALUES ('waiting_room_enabled', '0') ON CONFLICT (key) DO NOTHING",
    "INSERT INTO settings (key, value) VALUES ('meet_enabled', '0') ON CONFLICT (key) DO NOTHING",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS is_evicted INTEGER DEFAULT 0",
    "ALTER TABLE live_streams ADD COLUMN IF NOT EXISTS type_diffusion VARCHAR(50) DEFAULT 'standard'",
    '''CREATE TABLE IF NOT EXISTS webinaire_queue (
                        id SERIAL PRIMARY KEY,
                        live_id INTEGER NOT NULL,
                        user_email VARCHAR(255) NOT NULL,
                        display_name VARCHAR(255) NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(live_id, user_email)
                    )''',
    "CREATE INDEX IF NOT EXISTS idx_webinaire_queue_live_status ON webinaire_queue(live_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_webinaire_queue_live_user ON webinaire_queue(live_id, user_email)",
    "CREATE INDEX IF NOT EXISTS idx_medias_cat_comm ON medias(categorie, commission)",
    "CREATE INDEX IF NOT EXISTS idx_medias_series ON medias(series_id)",
    '''CREATE TABLE IF NOT EXISTS notes (
                        id SERIAL PRIMARY KEY,
                        user_email VARCHAR(255) NOT NULL,
                        media_id INTEGER,
                        media_title VARCHAR(255),
                        note_content TEXT NOT NULL,
                        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        date_modification TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''',
    '''CREATE TABLE IF NOT EXISTS messages (
                        id SERIAL PRIMARY KEY,
                        espace_name VARCHAR(100) NOT NULL,
                        user_email VARCHAR(255) NOT NULL,
                        user_name VARCHAR(255) NOT NULL,
                        user_role VARCHAR(100),
                        message_text TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''',
    '''CREATE TABLE IF NOT EXISTS pinned_messages (
                        id SERIAL PRIMARY KEY,
                        espace_name VARCHAR(100) NOT NULL,
                        user_email VARCHAR(255) NOT NULL,
                        user_name VARCHAR(255) NOT NULL,
                        user_role VARCHAR(100),
                        message_text TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''',
    '''CREATE TABLE IF NOT EXISTS espace_moderators (
                        id SERIAL PRIMARY KEY,
                        espace_name VARCHAR(100) NOT NULL,
                        user_email VARCHAR(255) NOT NULL,
                        nominated_by VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''',
    '''CREATE TABLE IF NOT EXISTS espace_visions (
                        espace_name VARCHAR(100) PRIMARY KEY,
                        vision_text TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''',
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_url VARCHAR(500)",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name VARCHAR(255)",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_type VARCHAR(100)",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_size INTEGER",
    "DELETE FROM webinaire_queue"
]

for idx, q in enumerate(queries):
    try:
        cur.execute(q)
        print(f"Query {idx} OK")
    except Exception as e:
        print(f"Query {idx} FAILED: {e}")

conn.commit()
conn.close()
