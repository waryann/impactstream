import psycopg2
from werkzeug.security import generate_password_hash

conn = psycopg2.connect('postgresql://postgres:ALjoFYRFOrFHehlP@db.vpbswejikerxfbebxuql.supabase.co:5432/postgres')
cur = conn.cursor()
password_hash = generate_password_hash('Masui128')

try:
    cur.execute("INSERT INTO users (email, password_hash, is_verified) VALUES (%s, %s, %s)", ('yann.noukaze@ministereimpact.org', password_hash, 1))
    print("Admin user created")
except psycopg2.IntegrityError:
    print("Admin user already exists")

conn.commit()
conn.close()
