with open('app.py', 'r') as f:
    c = f.read()

old_login = """
        # Étape 2 : Recherche en base
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        # Étape 3 : Comparaison mot de passe
        if user and check_password_hash(user['password_hash'], password):
"""

new_login = """
        # Étape 2 : Recherche en base
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        
        # Auto-inscription si le membre vient de l'Annuaire mais n'a pas encore de compte ImpactStream
        if not user:
            membre = conn.execute('SELECT * FROM membres WHERE email = ?', (email,)).fetchone()
            if membre:
                hashed_pw = generate_password_hash(password)
                v_token = str(uuid.uuid4())
                conn.execute(
                    'INSERT INTO users (email, password_hash, is_verified, verification_token) VALUES (?, ?, 1, ?)',
                    (email, hashed_pw, v_token)
                )
                conn.commit()
                user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        # Étape 3 : Comparaison mot de passe
        if user and check_password_hash(user['password_hash'], password):
"""

if old_login.strip() in c:
    c = c.replace(old_login.strip(), new_login.strip())
    with open('app.py', 'w') as f:
        f.write(c)
    print("Patch appliqué avec succès.")
else:
    print("Code introuvable dans app.py")
