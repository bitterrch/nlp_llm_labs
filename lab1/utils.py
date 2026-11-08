'''
Общие функции для лабы 1 (импортируются в ноутбук и в app.py):
клиент LLM, схема ProductQuery, промпты, поиск по SQLite, журнал и итоговый пайплайн с retry
'''
import json
import os
import socket
import sqlite3
import time
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from init_db import DB_PATH

BASE_URL = os.getenv('LLM_BASE_URL', 'http://10.32.2.11:35000/v1')
MODEL_NAME = os.getenv('LLM_MODEL', 'qwen3.8-27b')
TOKEN_ENV = 'LLM_API_KEY' # токен только в переменной окружения, в коде его нет
MAX_ATTEMPTS = 3 # попыток в with_retry, включая первую
LOGS_DIR = DB_PATH.parent / 'logs'


# ---------------------------------------------------------------- LLM

def get_llm(thinking=False, **kwargs):
  '''ChatOpenAI для сервера универа, thinking=False выключает режим размышления'''
  key = os.getenv(TOKEN_ENV)
  if not key:
    raise RuntimeError(f'нет токена, сделайте export {TOKEN_ENV}=<токен>')
  params = dict(model=MODEL_NAME, base_url=BASE_URL, api_key=key,
                temperature=0, # для разбора запросов нужна стабильность
                max_tokens=512, # лимит как в примере вызова с сервера универа
                timeout=180,
                max_retries=1, # ретраи сетевых ошибок внутри клиента, не путать с with_retry
                extra_body={'chat_template_kwargs': {'enable_thinking': thinking}})
  if thinking:
    # в режиме размышления qwen советуют не жадное декодирование, иначе может зациклиться
    params.update(temperature=0.6, top_p=0.95)
  params.update(kwargs)
  return ChatOpenAI(**params)


def server_available(timeout=3):
  '''Можно ли достучаться до сервера (мы в сети универа?)'''
  u = urlparse(BASE_URL)
  try:
    with socket.create_connection((u.hostname, u.port or 80), timeout=timeout):
      return True
  except OSError:
    return False


# ---------------------------------------------------------------- схема (2.1)

CATEGORY_RU = {'laptop': 'ноутбуки', 'smartphone': 'смартфоны', 'tablet': 'планшеты', 'other': 'другое'}
SUPPORTED_CATEGORIES = ('laptop', 'smartphone', 'tablet')


class ProductQuery(BaseModel):
  '''Параметры поиска товаров в каталоге магазина электроники'''
  # лишние поля запрещаем: иначе "price" вместо "max_price" молча превратится в цену по умолчанию
  model_config = ConfigDict(extra='forbid')

  category: Literal['laptop', 'smartphone', 'tablet', 'other'] = Field(
      description='Категория товара: laptop - ноутбуки, smartphone - смартфоны/телефоны, tablet - планшеты. '
                  'Если товар не из этих категорий (холодильник, наушники, телевизор и т.п.), то other.')
  max_price: Optional[int] = Field(
      default=None, ge=0,
      description='Верхняя граница цены в рублях, целое число ("до 50 тысяч" -> 50000). '
                  'null, если пользователь не ограничил цену.')
  sort_by: Literal['price_asc', 'price_desc'] = Field(
      default='price_asc',
      description='Сортировка по цене: price_asc - сначала дешёвые (по умолчанию), price_desc - сначала дорогие.')


def q_short(q):
  return '—' if q is None else f'{q.category}, {q.max_price}, {q.sort_by}'


# ---------------------------------------------------------------- промпты

parser = PydanticOutputParser(pydantic_object=ProductQuery)

RULES = '''Ты - модуль разбора запросов для интернет-магазина электроники.
Твоя задача - превратить запрос пользователя на естественном языке в параметры поиска.
В каталоге есть только три категории: ноутбуки (laptop), смартфоны (smartphone) и планшеты (tablet).
Правила:
- если пользователь ищет что-то другое (холодильник, наушники, телевизор и т.д.), то category = "other";
- цену переводи в рубли целым числом: "50 тысяч", "50к", "50 000 руб." -> 50000;
- "дешевле X", "до X", "не дороже X" означает max_price = X;
- если цена не названа, max_price = null;
- "самые дорогие", "сначала дорогие" -> sort_by = "price_desc", во всех остальных случаях "price_asc".'''

STATE_RULES = '''
Тебе передаются текущие параметры поиска и новая реплика пользователя.
Верни ОБНОВЛЁННЫЕ параметры: меняй только то, что пользователь просит изменить в новой реплике,
остальные параметры оставь как были. Если текущих параметров нет - это новый поиск.'''

JSON_ONLY = '''
Отвечай ТОЛЬКО JSON-объектом, без пояснений и без markdown.

{format_instructions}'''

TOOL_ONLY = '''
Чтобы найти товары, вызови инструмент search_products с подходящими аргументами.
Если сообщение пользователя вообще не про поиск товаров - просто ответь текстом.'''


def make_parse_prompt(stateful=False, tools=False):
  '''
  stateful - в промпт идут текущие параметры поиска (часть 5)
  tools - вместо инструкций формата просим вызвать инструмент (3.4)
  '''
  system = RULES + (STATE_RULES if stateful else '') + (TOOL_ONLY if tools else JSON_ONLY)
  human = 'Текущие параметры поиска: {current}\nНовая реплика пользователя: {query}' if stateful else '{query}'
  prompt = ChatPromptTemplate.from_messages([('system', system), ('human', human)])
  if not tools:
    # через partial, чтобы фигурные скобки JSON-схемы не считались переменными шаблона
    prompt = prompt.partial(format_instructions=parser.get_format_instructions())
  return prompt


# ---------------------------------------------------------------- поиск (3.2)

class UnsupportedCategoryError(ValueError):
  pass


ORDER_SQL = {'price_asc': 'ASC', 'price_desc': 'DESC'} # сортировку через ? не передать -> белый список


def build_sql(q):
  '''SQL из разрешённых полей, значения пользователя только через параметры ?'''
  sql = 'SELECT id, name, category, price FROM products WHERE category = ?'
  params = [q.category]
  if q.max_price is not None:
    sql += ' AND price <= ?'
    params.append(q.max_price)
  sql += f' ORDER BY price {ORDER_SQL[q.sort_by]}, id'
  return sql, params


def search_products(q, db_path=DB_PATH):
  '''Поиск по проверенному ProductQuery. Для other в базу не идём, иначе выдали бы весь каталог'''
  if q.category not in SUPPORTED_CATEGORIES:
    raise UnsupportedCategoryError(f'категория {q.category!r} не поддерживается')
  sql, params = build_sql(q)
  conn = sqlite3.connect(db_path)
  conn.row_factory = sqlite3.Row
  try:
    rows = conn.execute(sql, params).fetchall()
  finally:
    conn.close()
  return [dict(r) for r in rows]


MESSAGES = {'unsupported': 'Такие товары в каталоге не продаются. Есть только ноутбуки, смартфоны и планшеты.',
            'empty': 'Подходящих товаров не найдено.',
            'cancelled': 'Поиск отменён.'}


def make_result(status, q, items=()):
  items = list(items)
  msg = f'Найдено товаров: {len(items)}' if status == 'ok' else MESSAGES[status]
  return dict(status=status, query=q, items=items, message=msg)


def run_search(q, search_fn=search_products):
  '''Поиск + статус и сообщение для пользователя'''
  try:
    items = search_fn(q)
  except UnsupportedCategoryError:
    return make_result('unsupported', q)
  return make_result('ok' if items else 'empty', q, items)


def fmt_price(p):
  return f'{p:,}'.replace(',', ' ') + ' ₽'


def fmt_items(items, sep='; '):
  return sep.join(f"{it['name']} — {fmt_price(it['price'])}" for it in items)


def fmt_result(res):
  if res['status'] != 'ok':
    return res['message']
  return res['message'] + '\n  - ' + fmt_items(res['items'], sep='\n  - ')


@tool('search_products', args_schema=ProductQuery)
def search_products_tool(category, max_price=None, sort_by='price_asc'):
  '''Ищет товары в каталоге магазина электроники (ноутбуки, смартфоны, планшеты)
  по категории, максимальной цене в рублях и сортировке по цене.'''
  # сама модель инструмент не исполняет: мы проверяем аргументы и вызываем поиск сами
  return fmt_result(run_search(ProductQuery(category=category, max_price=max_price, sort_by=sort_by)))


# ---------------------------------------------------------------- журнал (4.3)

def hide_secrets(text):
  key = os.getenv(TOKEN_ENV)
  return text.replace(key, '***') if key else text


class Journal:
  '''Журнал шагов пайплайна, как лог у харнесса агента'''

  def __init__(self, name='run', echo=False, to_file=True):
    self.records, self.echo = [], echo
    self.request_id, self.attempt = 0, 0
    self.file = None
    if to_file:
      LOGS_DIR.mkdir(exist_ok=True)
      self.file = LOGS_DIR / f'{name}.log'
      self.file.write_text('', encoding='utf-8')

  def new_request(self, text):
    self.request_id += 1
    self.attempt = 0
    self.log('request', f'запрос пользователя: {text!r}')

  def log(self, step, msg='', **data):
    rec = dict(time=datetime.now().strftime('%H:%M:%S.%f')[:-3], req=self.request_id, step=step,
               msg=hide_secrets(str(msg)), **data)
    self.records.append(rec)
    line = self.fmt(rec)
    if self.echo:
      print(line)
    if self.file:
      with open(self.file, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

  @staticmethod
  def fmt(rec):
    return f"{rec['time']} | req={rec['req']} | {rec['step']:<14} | {rec['msg']}"

  def lines(self, req=None):
    return [self.fmt(r) for r in self.records if req is None or r['req'] == req]

  def show(self, req=None):
    print('\n'.join(self.lines(req)))

  def steps(self, req=None):
    return [r['step'] for r in self.records if req is None or r['req'] == req]

  def tokens(self, req, key='input_tokens'):
    '''Сумма токенов по всем вызовам LLM в запросе (с учётом ретраев)'''
    return sum(r.get(key) or 0 for r in self.records if r['req'] == req and r['step'] == 'llm_end')


# ---------------------------------------------------------------- пайплайн (4.2 - 5)

class NoToolCallError(Exception):
  '''Модель ответила текстом и не вызвала инструмент'''
  def __init__(self, text):
    super().__init__('модель не вызвала инструмент')
    self.text = text


def first_error(e):
  err = e.errors()[0]
  return f"{'.'.join(map(str, err['loc'])) or '(объект)'}: {err['msg']} (получено {err.get('input')!r})"


def make_llm_step(model, journal):
  def call_llm(prompt_value):
    journal.attempt += 1
    journal.log('llm_start', f'вызов LLM, попытка {journal.attempt}')
    t0 = time.perf_counter()
    msg = model.invoke(prompt_value)
    dt = time.perf_counter() - t0
    u = getattr(msg, 'usage_metadata', None) or {}
    journal.log('llm_end', f"ответ за {dt:.2f} с, токенов вход/выход: {u.get('input_tokens', '?')}/{u.get('output_tokens', '?')}",
                seconds=dt, input_tokens=u.get('input_tokens'), output_tokens=u.get('output_tokens'))
    return msg
  return RunnableLambda(call_llm, name='llm_step')


def make_parse_step(journal, tools=False):
  def parse_format(msg):
    # PydanticOutputParser: сначала текст -> json, потом json -> ProductQuery (валидация)
    try:
      q = parser.invoke(msg)
    except OutputParserException as e:
      if isinstance(e.__cause__, ValidationError):
        journal.log('validate_error', 'JSON разобран, но не прошёл проверку схемы: ' + first_error(e.__cause__))
      else:
        journal.log('parse_error', f'ответ модели не JSON: {msg.content[:100]!r}')
      raise
    journal.log('parse_ok', 'JSON разобран')
    journal.log('validate_ok', f'параметры прошли проверку ProductQuery: {q.model_dump_json()}')
    return q

  def parse_tool(msg):
    if msg.invalid_tool_calls and not msg.tool_calls:
      journal.log('parse_error', f"аргументы вызова не разобрались: {msg.invalid_tool_calls[0].get('args')!r}")
      raise OutputParserException('invalid tool call')
    if not msg.tool_calls:
      journal.log('no_tool_call', f'модель не вызвала инструмент, ответ текстом: {msg.content[:100]!r}')
      raise NoToolCallError(msg.content)
    call = msg.tool_calls[0]
    journal.log('parse_ok', f"вызов {call['name']}({json.dumps(call['args'], ensure_ascii=False)})")
    if call['name'] != 'search_products':
      journal.log('validate_error', f"неизвестный инструмент {call['name']}")
      raise OutputParserException(f"неизвестный инструмент {call['name']}")
    try:
      q = ProductQuery.model_validate(call['args'])
    except ValidationError as e:
      journal.log('validate_error', 'аргументы не прошли проверку схемы: ' + first_error(e))
      raise OutputParserException('bad tool args') from e
    journal.log('validate_ok', f'параметры прошли проверку ProductQuery: {q.model_dump_json()}')
    return q

  return RunnableLambda(parse_tool if tools else parse_format, name='parse_step')


def make_search_step(journal, confirm=None, search_fn=search_products):
  def step(q):
    # сюда попадает только объект, который уже прошёл проверку Pydantic
    if q.category not in SUPPORTED_CATEGORIES:
      journal.log('check', f'категория {q.category!r} не поддерживается -> в БД не идём')
      return make_result('unsupported', q)
    journal.log('check', 'категория поддерживается, параметры ок')
    if q.max_price is None and confirm is not None: # доп. задание: подтверждение без ограничения цены
      ok = confirm(q)
      journal.log('confirm', 'подтверждено: искать без ограничения цены' if ok else 'пользователь отказался')
      if not ok:
        return make_result('cancelled', q)
    journal.log('search_start', f'поиск в SQLite: {q_short(q)}')
    res = run_search(q, search_fn)
    journal.log('search_end', f"найдено товаров: {len(res['items'])}")
    return res
  return RunnableLambda(step, name='search_step')


def build_pipeline(model, journal, prompt=None, tools=False, retry=True, confirm=None,
                   search_fn=search_products, max_attempts=MAX_ATTEMPTS):
  '''
  prompt -> [LLM -> разбор и проверка]  -> проверка параметров -> поиск
             ^ только это повторяется через with_retry, поиск стоит снаружи и не повторяется
  '''
  if prompt is None:
    prompt = make_parse_prompt(tools=tools)
  if tools:
    model = model.bind_tools([search_products_tool])
  core = make_llm_step(model, journal) | make_parse_step(journal, tools)
  if retry:
    core = core.with_retry(stop_after_attempt=max_attempts,
                           retry_if_exception_type=(OutputParserException,), # только ошибки разбора
                           wait_exponential_jitter=False) # ждать незачем, это не rate limit
  return prompt | core | make_search_step(journal, confirm=confirm, search_fn=search_fn)


def ask(pipeline, journal, query, current=None, **extra):
  '''Один запрос через пайплайн, ожидаемые ошибки превращаем в понятное сообщение'''
  journal.new_request(query)
  inputs = dict(query=query, current=current.model_dump_json() if current is not None else 'нет (новый поиск)', **extra)
  try:
    res = pipeline.invoke(inputs)
  except OutputParserException:
    journal.log('fail', f'валидных параметров нет после {journal.attempt} попыт., поиск не выполнялся')
    return dict(status='error', query=None, items=[],
                message=f'Не получилось разобрать запрос ({journal.attempt} раз(а) некорректный ответ модели). '
                        'Попробуйте переформулировать.')
  except NoToolCallError as e:
    journal.log('fail', 'модель не вызвала инструмент, поиск не выполнялся')
    return dict(status='no_tool', query=None, items=[], message='Модель не стала искать и ответила: ' + e.text)
  journal.log('done', res['message'])
  return res
