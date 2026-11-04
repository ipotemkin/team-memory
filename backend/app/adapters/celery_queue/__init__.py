"""Адаптер очереди задач на Celery (брокер и backend результатов — Redis).

``queue.py`` — порт TaskQueue для API (ставит задачи по имени), ``worker.py`` — Celery-приложение
воркера с обработчиками задач и расписанием beat, ``health.py`` — проба Redis.
"""
