'''Задание 3.1: каталог товаров в SQLite. Запуск: python init_db.py (база каждый раз создаётся заново)'''
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / 'catalog.db'

# цены примерные, подбирала так, чтобы часть запросов давала пустой результат
# (ноутбуков дешевле 54 990 нет, смартфонов дешевле 18 990 нет)
PRODUCTS = [
  ('Lenovo IdeaPad Slim 3 15', 'laptop', 54990),
  ('ASUS Vivobook 16', 'laptop', 61490),
  ('Acer Aspire 5', 'laptop', 67990),
  ('HUAWEI MateBook D 16', 'laptop', 74990),
  ('HONOR MagicBook X16', 'laptop', 79990),
  ('Apple MacBook Air 13 M3', 'laptop', 124990),
  ('ASUS ROG Strix G16', 'laptop', 159990),

  ('Xiaomi Redmi Note 13', 'smartphone', 18990),
  ('POCO X6 Pro', 'smartphone', 27990),
  ('realme 12 Pro+', 'smartphone', 32490),
  ('Samsung Galaxy A55', 'smartphone', 34990),
  ('Apple iPhone 15', 'smartphone', 79990),
  ('Samsung Galaxy S24 Ultra', 'smartphone', 119990),
  ('Apple iPhone 16 Pro Max', 'smartphone', 139990),

  ('Xiaomi Redmi Pad SE', 'tablet', 14990),
  ('HUAWEI MatePad 11.5', 'tablet', 29990),
  ('Samsung Galaxy Tab S9 FE', 'tablet', 39990),
  ('Apple iPad Air 11 M2', 'tablet', 69990),
  ('Apple iPad Pro 13 M4', 'tablet', 149990)
]

def init_db(path=DB_PATH):
  conn = sqlite3.connect(path)
  conn.execute('DROP TABLE IF EXISTS products')
  conn.execute('''
    CREATE TABLE products (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      category TEXT NOT NULL CHECK (category IN ('laptop', 'smartphone', 'tablet')),
      price INTEGER NOT NULL CHECK (price >= 0)
    )''')
  conn.executemany('INSERT INTO products (name, category, price) VALUES (?, ?, ?)', PRODUCTS)
  conn.commit()
  conn.close()

if __name__ == '__main__':
  init_db()
  print(f'база создана: {DB_PATH}, товаров: {len(PRODUCTS)}')
