import sqlite3
import os
import datetime
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medibridge.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")  # Enable WAL mode for high concurrency
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pharmacies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        locality TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pharmacy_id INTEGER NOT NULL,
        medicine_name TEXT NOT NULL,
        salt TEXT NOT NULL,
        is_available BOOLEAN NOT NULL DEFAULT 1,
        last_updated TEXT NOT NULL,
        FOREIGN KEY (pharmacy_id) REFERENCES pharmacies (id)
    );
    """)

    # Seed data if empty
    cursor.execute("SELECT COUNT(*) FROM pharmacies;")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
            INSERT INTO pharmacies (name, locality, lat, lng) VALUES (?, ?, ?, ?);
        """, [
            ("Sharma Medicos", "Rohini Sector 14, Delhi", 28.7165, 77.1154),
            ("Aggarwal Pharmacy", "Rohini Sector 14, Delhi", 28.7180, 77.1140),
            ("Gupta Chemist", "Rohini Sector 15, Delhi", 28.7210, 77.1190)
        ])

        now_str = datetime.datetime.now().strftime("%I:%M %p")
        # Sample inventory entries
        cursor.executemany("""
            INSERT INTO inventory (pharmacy_id, medicine_name, salt, is_available, last_updated) VALUES (?, ?, ?, ?, ?);
        """, [
            (1, "Dolo 650", "Paracetamol 650mg", 1, now_str),
            (1, "Azithral 500", "Azithromycin 500mg", 1, now_str),
            (2, "Calpol 650", "Paracetamol 650mg", 1, now_str),
            (2, "Dolo 650", "Paracetamol 650mg", 1, now_str),
            (3, "Pan 40", "Pantoprazole 40mg", 1, now_str),
            (3, "Calpol 650", "Paracetamol 650mg", 1, now_str)
        ])

    conn.commit()
    conn.close()

def search_medicine(query: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    q = f"%{query.strip()}%"

    cursor.execute("""
        SELECT p.name, p.locality, p.lat, p.lng, i.medicine_name, i.salt, i.is_available, i.last_updated
        FROM inventory i
        JOIN pharmacies p ON i.pharmacy_id = p.id
        WHERE i.medicine_name LIKE ? OR i.salt LIKE ?
    """, (q, q))

    rows = cursor.fetchall()
    results = []

    matched_salts = set()
    for row in rows:
        matched_salts.add(row[5])
        results.append({
            "pharmacy_name": row[0],
            "locality": row[1],
            "lat": row[2],
            "lng": row[3],
            "medicine_name": row[4],
            "salt": row[5],
            "is_available": bool(row[6]),
            "last_updated": row[7],
            "is_substitute": False,
            "substitute_note": None
        })

    # If the searched brand is out of stock in all shops or specific shop, suggest identical salt alternatives
    if matched_salts:
        for salt in matched_salts:
            cursor.execute("""
                SELECT p.name, p.locality, p.lat, p.lng, i.medicine_name, i.salt, i.is_available, i.last_updated
                FROM inventory i
                JOIN pharmacies p ON i.pharmacy_id = p.id
                WHERE i.salt = ? AND i.medicine_name NOT LIKE ? AND i.is_available = 1
            """, (salt, q))
            sub_rows = cursor.fetchall()
            for s in sub_rows:
                results.append({
                    "pharmacy_name": s[0],
                    "locality": s[1],
                    "lat": s[2],
                    "lng": s[3],
                    "medicine_name": s[4],
                    "salt": s[5],
                    "is_available": True,
                    "last_updated": s[7],
                    "is_substitute": True,
                    "substitute_note": f"Alternative with identical {s[5]} formulation. Confirm at counter."
                })

    conn.close()
    return results

def update_stock(pharmacy_id: int, medicine_name: str, is_available: bool) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%I:%M %p")

    cursor.execute("""
        UPDATE inventory
        SET is_available = ?, last_updated = ?
        WHERE pharmacy_id = ? AND medicine_name LIKE ?
    """, (1 if is_available else 0, now_str, pharmacy_id, f"%{medicine_name.strip()}%"))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated

# Automatically initialize database when imported
init_db()
