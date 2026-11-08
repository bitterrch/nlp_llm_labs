# Лабораторная работа №1 - пайплайны на LangChain + LLM (поиск по каталогу)

| Файл | Что внутри |
|---|---|
| `Lab1_Novikova.ipynb` | все эксперименты по частям 1–5, выводы, ответы на контрольные вопросы |
| `utils.py` | общий код: клиент LLM, схема `ProductQuery`, промпты, поиск по SQLite, журнал, пайплайн с retry |
| `init_db.py` | создание `catalog.db` (19 товаров) |
| `app.py` | итоговая программа (консоль) |
| `requirements.txt` | зависимости |
| `report.md` | отчет о выполненной работе |

## Установка

Нужен Python 3.10+

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Токен

Токен в коде нигде не написан, он берётся из переменной окружения `LLM_API_KEY`:

```bash
export LLM_API_KEY=<свой токен>
```

## Запуск

```bash
jupyter notebook Lab1_Novikova.ipynb
```

дальше Run All. Журналы пишутся в `logs/`, результаты тестов 2.3 — в `tests_2_3.json`.

Итоговая программа:

```bash
python app.py # диалог, можно уточнять запрос
python app.py --tools # параметры через вызов инструмента
python app.py --verbose # печатать журнал
python app.py "Покажи ноутбуки до 80 тысяч" # один запрос
```

Команды в диалоге: `/new` - новый поиск, `/log` - журнал последнего запроса, `/exit` - выход.

## Отчет

Отчет о выполненной работе лежит в файле `report.md`
