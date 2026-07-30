from app import init_db
try:
    init_db()
    print("DB Init OK")
except Exception as e:
    print(f"Failed: {e}")
