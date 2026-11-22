# Публикация на GitHub и хостинг на Railway

## 1. Переменные окружения для Railway

| Переменная | Обязательна | Значение |
|---|---|---|
| `BOT_TOKEN` | да | токен от @BotFather |
| `DATABASE_URL` | да | ссылка на Postgres Railway: `${{Postgres.DATABASE_URL}}` (имя сервиса должно совпадать с вашей БД) |
| `ADMIN_IDS` | да | Telegram ID админов через запятую: `123456789,987654321` |
| `TIMEZONE` | нет | например `Europe/Kyiv` (для «продажи сегодня» и дат) |
| `DEFAULT_SUPPORT` | нет | `@cryptcursed` (потом меняется в `/adm → Настройки`) |
| `DEFAULT_SHOP_NAME` | нет | название магазина |

`PORT` не нужен: бот работает через long polling и ничего не слушает.
Свой ID узнайте у @userinfobot.

## 2. Публикация на GitHub

1. Распакуйте `shopbot.zip`. Убедитесь, что файла `.env` в папке нет (в репозиторий идёт только `.env.example`; `.gitignore` уже исключает `.env`).
2. На github.com создайте новый репозиторий, лучше **Private**, без README.
3. В папке проекта:
```bash
git init
git add .
git status          # проверьте: .env в списке быть не должно
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<ваш-логин>/<репозиторий>.git
git push -u origin main
```
4. Токен бота никогда не коммитьте. Если он случайно попал в репозиторий, сразу выпустите новый: @BotFather → `/revoke`.

## 3. Деплой на Railway

1. railway.app → **New Project** → **Deploy from GitHub repo** → выберите репозиторий (разрешите Railway доступ к GitHub).
2. В проекте: **+ New** → **Database** → **PostgreSQL**.
3. Откройте сервис бота → **Variables** → добавьте переменные из таблицы выше. Для `DATABASE_URL` вставьте `${{Postgres.DATABASE_URL}}`.
4. Railway соберёт образ по `Dockerfile` (настройки в `railway.json`) и запустит `python main.py`. Схема БД создастся при первом старте.
5. Во вкладке **Deployments → Logs** должно быть `Start polling`. Напишите боту `/start`, затем `/adm`.
6. Каждый `git push` в `main` будет автоматически выкатывать новую версию.

## 4. Важно

* **Одна реплика.** Запускайте ровно один экземпляр (в `railway.json` стоит `numReplicas: 1`). Два бота с одним токеном вызывают ошибку `Conflict: terminated by other getUpdates`. Не запускайте бота одновременно локально и на Railway.
* **Состояние анкет в памяти:** при каждом редеплое незавершённые анкеты админа сбрасываются. Товары, покупки и баланс хранятся в Postgres и не теряются.
* **Резервные копии:** включите бэкапы Postgres в Railway или периодически делайте `pg_dump`: там хранятся данные товаров и финансовые записи.
* Если в логах `password authentication failed` или `connection refused`, проверьте, что `DATABASE_URL` указывает на сервис Postgres именно этого проекта.
