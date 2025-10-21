import sqlite3

DB_NAME = 'admins.db'

def init_db():
    """Initializes the database and creates the sudo_users table if it doesn't exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sudo_users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def add_sudo(user_id: int) -> bool:
    """Adds a user to the sudo list. Returns True if successful, False if user already exists."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO sudo_users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # User already exists
        return False
    finally:
        conn.close()

def del_sudo(user_id: int) -> bool:
    """Removes a user from the sudo list. Returns True if user was deleted, False otherwise."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM sudo_users WHERE user_id = ?', (user_id,))
    # The changes attribute tells us how many rows were affected
    changes = conn.total_changes
    conn.commit()
    conn.close()
    return changes > 0

def is_sudo(user_id: int) -> bool:
    """Checks if a user is in the sudo list."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sudo_users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_all_sudos() -> list[int]:
    """Retrieves a list of all sudo user IDs from the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM sudo_users ORDER BY user_id')
    # The result of fetchall is a list of tuples, e.g., [(123,), (456,)]
    # We convert it to a simple list of integers.
    user_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return user_ids
