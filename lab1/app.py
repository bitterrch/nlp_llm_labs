'''
Итоговая программа: поиск по каталогу на естественном языке
  python app.py                 - диалог (можно уточнять запрос), параметры через инструкции формата
  python app.py --tools         - параметры через вызов инструмента
  python app.py --verbose       - печатать журнал
  python app.py "Покажи ноутбуки до 80 тысяч"   - один запрос
Команды: /new - новый поиск, /log - журнал последнего запроса, /exit - выход
'''
import argparse

from init_db import DB_PATH, init_db
from utils import CATEGORY_RU, Journal, ask, build_pipeline, fmt_result, get_llm, make_parse_prompt

def confirm_no_price(q):
  ans = input(f'цена не ограничена - показать все {CATEGORY_RU[q.category]}? [Y/n] ').strip().lower()
  return ans in ('', 'y', 'yes', 'д', 'да')

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument('query', nargs='*')
  ap.add_argument('--tools', action='store_true')
  ap.add_argument('--verbose', action='store_true')
  ap.add_argument('--no-confirm', action='store_true')
  args = ap.parse_args()

  if not DB_PATH.exists():
    init_db()
  journal = Journal('app', echo=args.verbose)
  pipe = build_pipeline(get_llm(), journal, prompt=make_parse_prompt(stateful=True, tools=args.tools),
                        tools=args.tools, confirm=None if args.no_confirm else confirm_no_price)
  current = None # состояние диалога - последний валидный ProductQuery

  def handle(text):
    nonlocal current
    try:
      res = ask(pipe, journal, text, current=current)
    except Exception as e: # сеть, сервер и т.п.
      print(f'ошибка при обращении к модели: {type(e).__name__}: {e}')
      return
    if res['query'] is not None:
      current = res['query']
      print(f'(параметры: {current.model_dump_json()})')
    print(fmt_result(res))

  if args.query:
    handle(' '.join(args.query))
    return

  print('Поиск по каталогу электроники. /new - новый поиск, /log - журнал, /exit - выход')
  while True:
    try:
      text = input('\n> ').strip()
    except (EOFError, KeyboardInterrupt):
      break
    if text in ('/exit', '/quit'):
      break
    elif text == '/new':
      current = None
      print('параметры сброшены')
    elif text == '/log':
      journal.show(journal.request_id)
    elif text:
      handle(text)

if __name__ == '__main__':
  main()
