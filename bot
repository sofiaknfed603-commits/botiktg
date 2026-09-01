import requests
import os
import sys
import re
import time
import logging
import base64
import json
import sqlite3
from datetime import datetime
from urllib.parse import quote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ============ КОНФИГУРАЦИЯ ============
GITLAB_URL = "https://gitlab.com"
PRIVATE_TOKEN = "glpat-RgsZcQsAAoZLpu1Xbzx2UmM6MQpvOjEKdTpvd3N3Yw8.01.171n3g7pl"
PROJECT_ID = "85897480"

# Telegram Bot Token
TELEGRAM_TOKEN = "8940213656:AAEHr3mf-9tmrPNHs56gu4e8T90ojpps85A"

# ПУТЬ К ФАЙЛУ В РЕПОЗИТОРИИ
FILE_PATH = ".gitlab/service_desk_templates/new_participant.md"
BRANCH = "main"

# Размер пачки (фиксированный)
BATCH_SIZE = 10

# ID администраторов (Telegram User IDs)
ADMIN_IDS = [8309166159]  # ID администратора

# =====================================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Временное хранилище для пользовательских сессий
user_sessions = {}

# ============ БАЗА ДАННЫХ ============

class Database:
    def __init__(self, db_file="bot_database.db"):
        self.db_file = db_file
        self.init_db()
    
    def get_connection(self):
        """Получить соединение с БД"""
        return sqlite3.connect(self.db_file)
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Таблица пользователей (белый список)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица для логов действий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Таблица для статистики использования
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                command TEXT,
                emails_count INTEGER,
                issues_created INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Добавляем администраторов в БД, если их нет
        for admin_id in ADMIN_IDS:
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, is_admin, is_active)
                VALUES (?, ?, ?, ?)
            ''', (admin_id, f"admin_{admin_id}", 1, 1))
        
        conn.commit()
        conn.close()
        logger.info("База данных инициализирована")
    
    def is_user_allowed(self, user_id):
        """Проверить, есть ли пользователь в белом списке"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT is_active FROM users WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def is_admin(self, user_id):
        """Проверить, является ли пользователь администратором"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT is_admin FROM users WHERE user_id = ? AND is_admin = 1
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_user(self, user_id, username=None, first_name=None, last_name=None):
        """Добавить пользователя в белый список"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, is_active)
                VALUES (?, ?, ?, ?, 1)
            ''', (user_id, username, first_name, last_name))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления пользователя: {e}")
            return False
        finally:
            conn.close()
    
    def remove_user(self, user_id):
        """Удалить пользователя из белого списка"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE users SET is_active = 0 WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления пользователя: {e}")
            return False
        finally:
            conn.close()
    
    def get_all_users(self, active_only=True):
        """Получить список всех пользователей"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if active_only:
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, is_admin, registered_at, last_active
                FROM users WHERE is_active = 1 ORDER BY registered_at DESC
            ''')
        else:
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, is_admin, registered_at, last_active
                FROM users ORDER BY registered_at DESC
            ''')
        results = cursor.fetchall()
        conn.close()
        return results
    
    def log_action(self, user_id, action, details=""):
        """Записать лог действия"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO activity_logs (user_id, action, details)
                VALUES (?, ?, ?)
            ''', (user_id, action, details))
            cursor.execute('''
                UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка записи лога: {e}")
        finally:
            conn.close()
    
    def log_usage(self, user_id, command, emails_count=0, issues_created=0):
        """Записать статистику использования"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO usage_stats (user_id, command, emails_count, issues_created)
                VALUES (?, ?, ?, ?)
            ''', (user_id, command, emails_count, issues_created))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка записи статистики: {e}")
        finally:
            conn.close()
    
    def get_stats(self):
        """Получить общую статистику"""
        conn = self.get_connection()
        cursor = conn.cursor()
        stats = {}
        
        # Общее количество пользователей
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
        stats['total_users'] = cursor.fetchone()[0]
        
        # Количество администраторов
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1')
        stats['total_admins'] = cursor.fetchone()[0]
        
        # Общее количество использований
        cursor.execute('SELECT COUNT(*) FROM usage_stats')
        stats['total_commands'] = cursor.fetchone()[0]
        
        # Общее количество обработанных email
        cursor.execute('SELECT SUM(emails_count) FROM usage_stats')
        total_emails = cursor.fetchone()[0]
        stats['total_emails'] = total_emails if total_emails else 0
        
        # Количество созданных Issues
        cursor.execute('SELECT SUM(issues_created) FROM usage_stats')
        total_issues = cursor.fetchone()[0]
        stats['total_issues'] = total_issues if total_issues else 0
        
        conn.close()
        return stats

# Инициализация базы данных
db = Database()

# ============ ФУНКЦИИ ДЛЯ РАБОТЫ С GITLAB ============

def parse_emails(email_input):
    """Парсинг email из строки с разделителями"""
    email_list = re.split(r'[,;\s\n]+', email_input)
    emails = []
    for email in email_list:
        email = email.strip()
        if email and '@' in email and '.' in email:
            emails.append(email)
    return emails

def create_issue(title, description=""):
    """Создание нового Issue"""
    url = f"{GITLAB_URL}/api/v4/projects/{PROJECT_ID}/issues"
    headers = {
        "PRIVATE-TOKEN": PRIVATE_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "title": title,
        "description": description
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        issue_data = response.json()
        issue_id = issue_data.get("iid")
        issue_url = issue_data.get("web_url")
        return True, issue_id, issue_url
    except Exception as e:
        return False, None, str(e)

def add_single_email_to_issue(issue_id, email, delay=0.3):
    """Добавление одного email через команду /add_email"""
    comment = f"/add_email {email}"
    
    url = f"{GITLAB_URL}/api/v4/projects/{PROJECT_ID}/issues/{issue_id}/notes"
    headers = {
        "PRIVATE-TOKEN": PRIVATE_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "body": comment
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        time.sleep(delay)
        return True, f"✅ {email}"
    except Exception as e:
        return False, f"❌ {email} - {str(e)[:50]}"

def process_emails_in_batches(emails, batch_size=BATCH_SIZE, issue_name="New action"):
    """Обработка email пачками с кастомным именем Issue"""
    total_emails = len(emails)
    batches = [emails[i:i+batch_size] for i in range(0, total_emails, batch_size)]
    
    results = []
    total_success = 0
    total_errors = 0
    
    for batch_num, batch in enumerate(batches, 1):
        issue_title = f"{issue_name} #{batch_num}"
        
        success, issue_id, issue_url = create_issue(issue_title)
        
        if not success:
            results.append({
                "batch": batch_num,
                "status": "error",
                "message": issue_url
            })
            continue
        
        batch_results = []
        for email in batch:
            success, message = add_single_email_to_issue(issue_id, email)
            batch_results.append({
                "email": email,
                "success": success,
                "message": message
            })
            if success:
                total_success += 1
            else:
                total_errors += 1
        
        results.append({
            "batch": batch_num,
            "issue_id": issue_id,
            "issue_url": issue_url,
            "issue_title": issue_title,
            "emails": batch,
            "results": batch_results,
            "status": "success"
        })
        
        if batch_num < len(batches):
            time.sleep(2)
    
    return results, total_success, total_errors

def encode_file_path(file_path):
    """Кодирование пути файла для URL"""
    return quote(file_path, safe='')

def get_file_content(file_path=FILE_PATH, branch=BRANCH):
    """Получить содержимое файла из репозитория"""
    encoded_path = encode_file_path(file_path)
    url = f"{GITLAB_URL}/api/v4/projects/{PROJECT_ID}/repository/files/{encoded_path}"
    params = {
        "ref": branch,
        "private_token": PRIVATE_TOKEN
    }
    
    try:
        response = requests.get(url, params=params)
        
        if response.status_code == 404:
            return None, f"Файл {file_path} не найден в ветке {branch}"
        
        response.raise_for_status()
        file_data = response.json()
        content = base64.b64decode(file_data.get("content", "")).decode("utf-8")
        return content, None
    except Exception as e:
        logger.error(f"Ошибка при чтении файла: {e}")
        return None, str(e)

def update_file_content(new_content, file_path=FILE_PATH, branch=BRANCH, commit_message="Обновление файла через Telegram бота"):
    """Обновить содержимое файла в репозитории"""
    encoded_path = encode_file_path(file_path)
    url = f"{GITLAB_URL}/api/v4/projects/{PROJECT_ID}/repository/files/{encoded_path}"
    headers = {
        "PRIVATE-TOKEN": PRIVATE_TOKEN,
        "Content-Type": "application/json"
    }
    
    encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
    
    data = {
        "branch": branch,
        "content": encoded_content,
        "commit_message": commit_message,
        "encoding": "base64"
    }
    
    try:
        response = requests.put(url, headers=headers, json=data)
        response.raise_for_status()
        return True, None
    except Exception as e:
        logger.error(f"Ошибка при обновлении файла: {e}")
        return False, str(e)

# ============ ДЕКОРАТОР ДЛЯ ПРОВЕРКИ ДОСТУПА ============

def check_access(func):
    """Декоратор для проверки доступа пользователя"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        # Проверяем, есть ли пользователь в белом списке
        if not db.is_user_allowed(user_id):
            # Проверяем, не является ли пользователь администратором
            if user_id in ADMIN_IDS:
                db.add_user(user_id, update.effective_user.username, 
                           update.effective_user.first_name, update.effective_user.last_name)
            else:
                await update.message.reply_text(
                    "❌ У вас нет доступа к этому боту.\n\n"
                    "Пожалуйста, обратитесь к администратору для получения доступа.\n"
                    f"Ваш ID: {user_id}"
                )
                return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

def admin_only(func):
    """Декоратор для команд, доступных только администраторам"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        if not db.is_admin(user_id):
            await update.message.reply_text(
                "❌ Эта команда доступна только администраторам."
            )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

# ============ TELEGRAM BOT HANDLERS ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = user.id
    
    # Проверяем доступ пользователя
    if not db.is_user_allowed(user_id):
        # Проверяем, является ли пользователь администратором
        if user_id in ADMIN_IDS:
            db.add_user(user_id, user.username, user.first_name, user.last_name)
            db.log_action(user_id, "start", "Администратор запустил бота")
            is_admin = True
        else:
            await update.message.reply_text(
                "❌ У вас нет доступа к этому боту.\n\n"
                "Пожалуйста, обратитесь к администратору для получения доступа.\n"
                f"Ваш ID: {user_id}\n\n"
                "Администратор может добавить вас командой:\n"
                f"/adduser {user_id}"
            )
            return
    else:
        db.log_action(user_id, "start", "Пользователь запустил бота")
        is_admin = db.is_admin(user_id)
    
    welcome_text = (
        "🤖 GitLab Issue Creator Bot\n\n"
        "Я помогу вам:\n"
        "1. Создавать Issues и добавлять пользователей пачками по 10 email\n"
        "2. Редактировать файл в репозитории\n\n"
        f"📁 Текущий файл: {FILE_PATH}\n"
        f"🌿 Ветка: {BRANCH}\n\n"
        "📌 Команды:\n"
        "/start - Показать это сообщение\n"
        "/help - Помощь\n"
        "/file - Показать содержимое файла\n"
        "/edit - Редактировать файл\n"
        "/cancel - Отменить текущую операцию"
    )
    
    if is_admin:
        welcome_text += "\n\n🔐 Админ-панель:\n"
        welcome_text += "/admin - Админ-панель\n"
        welcome_text += "/users - Список пользователей\n"
        welcome_text += "/stats - Статистика бота"
    
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    user_id = update.effective_user.id
    
    # Проверяем доступ
    if not db.is_user_allowed(user_id):
        await update.message.reply_text(
            "❌ У вас нет доступа к этому боту.\n\n"
            "Пожалуйста, обратитесь к администратору для получения доступа."
        )
        return
    
    db.log_action(user_id, "help", "Пользователь запросил помощь")
    
    help_text = (
        "📖 Помощь\n\n"
        "1. Добавление пользователей:\n"
        "   Отправьте список email через запятую\n"
        "   Затем введите имя для Issues (по умолчанию 'New action')\n"
        "   Email будут автоматически распределены по 10 штук в один Issue\n\n"
        "2. Работа с файлом:\n"
        f"   Текущий файл: {FILE_PATH}\n"
        "   /file - показать текущее содержимое\n"
        "   /edit - начать редактирование файла\n\n"
        "⚙️ Команды:\n"
        "/start - Главное меню\n"
        "/help - Помощь\n"
        "/file - Показать файл\n"
        "/edit - Редактировать файл\n"
        "/cancel - Отменить операцию"
    )
    
    if db.is_admin(user_id):
        help_text += "\n\n🔐 Админ-команды:\n"
        help_text += "/admin - Админ-панель\n"
        help_text += "/users - Список пользователей\n"
        help_text += "/stats - Статистика\n"
        help_text += "/adduser <user_id> - Добавить пользователя\n"
        help_text += "/removeuser <user_id> - Удалить пользователя"
    
    await update.message.reply_text(help_text)

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ-панель"""
    user_id = update.effective_user.id
    db.log_action(user_id, "admin_panel", "Открыта админ-панель")
    
    stats = db.get_stats()
    
    keyboard = [
        [InlineKeyboardButton("Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton("Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("Добавить пользователя", callback_data="admin_add_user")],
        [InlineKeyboardButton("Удалить пользователя", callback_data="admin_remove_user")],
        [InlineKeyboardButton("Логи действий", callback_data="admin_logs")],
        [InlineKeyboardButton("Закрыть", callback_data="admin_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "АДМИН-ПАНЕЛЬ\n\n"
        f"Всего пользователей: {stats['total_users']}\n"
        f"Администраторов: {stats['total_admins']}\n"
        f"Всего команд: {stats['total_commands']}\n"
        f"Обработано email: {stats['total_emails']}\n"
        f"Создано Issues: {stats['total_issues']}\n\n"
        "Выберите действие:"
    )
    
    await update.message.reply_text(text, reply_markup=reply_markup)

@admin_only
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список пользователей"""
    user_id = update.effective_user.id
    db.log_action(user_id, "list_users", "Просмотр списка пользователей")
    
    users = db.get_all_users(active_only=True)
    
    if not users:
        await update.message.reply_text("Нет активных пользователей.")
        return
    
    text = "СПИСОК ПОЛЬЗОВАТЕЛЕЙ:\n\n"
    for user in users[:20]:
        user_id_db, username, first_name, last_name, is_admin, reg_date, last_active = user
        status = "АДМИН" if is_admin else "ПОЛЬЗОВАТЕЛЬ"
        name = first_name or username or str(user_id_db)
        text += f"ID: {user_id_db}\n"
        text += f"Имя: {name}\n"
        text += f"Статус: {status}\n"
        text += f"Регистрация: {reg_date[:16]}\n"
        text += f"Активность: {last_active[:16]}\n"
        text += "-" * 30 + "\n"
    
    if len(users) > 20:
        text += f"\n... и еще {len(users) - 20} пользователей"
    
    await update.message.reply_text(text)

@admin_only
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику бота"""
    user_id = update.effective_user.id
    db.log_action(user_id, "show_stats", "Просмотр статистики")
    
    stats = db.get_stats()
    
    text = (
        "СТАТИСТИКА БОТА\n\n"
        f"Всего пользователей: {stats['total_users']}\n"
        f"Администраторов: {stats['total_admins']}\n"
        f"Всего команд: {stats['total_commands']}\n"
        f"Обработано email: {stats['total_emails']}\n"
        f"Создано Issues: {stats['total_issues']}\n"
    )
    
    await update.message.reply_text(text)

@admin_only
async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить пользователя через команду"""
    user_id = update.effective_user.id
    
    try:
        new_user_id = int(context.args[0]) if context.args else None
        if not new_user_id:
            await update.message.reply_text(
                "Использование: /adduser <user_id>\n"
                "Пример: /adduser 123456789"
            )
            return
        
        # Получаем информацию о пользователе через Telegram API
        try:
            chat = await context.bot.get_chat(new_user_id)
            username = chat.username
            first_name = chat.first_name
            last_name = chat.last_name
        except:
            username = None
            first_name = None
            last_name = None
        
        if db.add_user(new_user_id, username, first_name, last_name):
            db.log_action(user_id, "add_user", f"Добавлен пользователь {new_user_id}")
            await update.message.reply_text(
                f"✅ Пользователь {new_user_id} успешно добавлен в белый список!"
            )
        else:
            await update.message.reply_text("❌ Ошибка при добавлении пользователя.")
    
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Использование: /adduser <user_id>\n"
            "Пример: /adduser 123456789"
        )

@admin_only
async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить пользователя через команду"""
    user_id = update.effective_user.id
    
    try:
        remove_user_id = int(context.args[0]) if context.args else None
        if not remove_user_id:
            await update.message.reply_text(
                "Использование: /removeuser <user_id>\n"
                "Пример: /removeuser 123456789"
            )
            return
        
        if remove_user_id in ADMIN_IDS:
            await update.message.reply_text(
                "❌ Нельзя удалить администратора из белого списка!"
            )
            return
        
        if db.remove_user(remove_user_id):
            db.log_action(user_id, "remove_user", f"Удален пользователь {remove_user_id}")
            await update.message.reply_text(
                f"✅ Пользователь {remove_user_id} удален из белого списка!"
            )
        else:
            await update.message.reply_text("❌ Ошибка при удалении пользователя.")
    
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Использование: /removeuser <user_id>\n"
            "Пример: /removeuser 123456789"
        )

@check_access
async def show_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать содержимое файла"""
    user_id = update.effective_user.id
    db.log_action(user_id, "show_file", "Просмотр файла")
    
    await update.message.reply_text("Загружаю содержимое файла...")
    
    content, error = get_file_content()
    
    if error:
        if "не найден" in error:
            await update.message.reply_text(
                f"Файл {FILE_PATH} не найден!\n\n"
                f"Проверьте:\n"
                f"1. Правильный ли путь к файлу\n"
                f"2. Существует ли ветка {BRANCH}\n"
                f"3. Есть ли доступ к репозиторию\n\n"
                f"Создайте файл вручную в репозитории или через веб-интерфейс GitLab."
            )
        else:
            await update.message.reply_text(f"Ошибка при чтении файла:\n\n{error}")
        return
    
    if len(content) > 4000:
        content = content[:4000] + "\n\n... (файл обрезан)"
    
    await update.message.reply_text(
        f"Содержимое файла:\n"
        f"Файл: {FILE_PATH}\n\n"
        f"```\n{content}\n```",
        parse_mode='Markdown'
    )

@check_access
async def edit_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование файла"""
    user_id = update.effective_user.id
    db.log_action(user_id, "edit_file_start", "Начало редактирования файла")
    
    await update.message.reply_text("Проверяю существование файла...")
    
    content, error = get_file_content()
    
    if error:
        if "не найден" in error:
            await update.message.reply_text(
                f"Файл {FILE_PATH} не найден!\n\n"
                f"Создайте файл вручную в репозитории через веб-интерфейс GitLab."
            )
            return
        else:
            await update.message.reply_text(f"Ошибка: {error}")
            return
    
    user_sessions[user_id] = {
        "step": "editing_file",
        "original_content": content
    }
    
    await update.message.reply_text(
        f"Редактирование файла {FILE_PATH}\n\n"
        "Отправьте новый текст файла.\n"
        "Используйте Markdown для форматирования.\n\n"
        "Текущее содержимое:\n"
        f"```\n{content[:500]}...\n```\n\n"
        "Для отмены используйте /cancel"
    )

@check_access
async def save_file_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить измененный файл"""
    user_id = update.effective_user.id
    new_content = update.message.text
    
    if user_id not in user_sessions:
        await update.message.reply_text("Сессия истекла. Используйте /edit для начала.")
        return
    
    await update.message.reply_text("Сохраняю файл...")
    
    success, error = update_file_content(new_content)
    
    if success:
        db.log_action(user_id, "save_file", "Файл успешно сохранен")
        await update.message.reply_text("Файл успешно обновлен!")
    else:
        await update.message.reply_text(f"Ошибка при сохранении:\n\n{error}")
    
    if user_id in user_sessions:
        del user_sessions[user_id]

@check_access
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cancel"""
    user_id = update.effective_user.id
    
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("Операция отменена")
    else:
        await update.message.reply_text("Нет активной операции")

@check_access
async def handle_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученных email"""
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id in user_sessions and user_sessions[user_id].get("step") == "editing_file":
        await save_file_edit(update, context)
        return
    
    emails = parse_emails(text)
    
    if not emails:
        await update.message.reply_text(
            "Не найдено корректных email!\n"
            "Пожалуйста, отправьте email через запятую, пробел или новой строкой."
        )
        return
    
    user_sessions[user_id] = {
        "emails": emails,
        "step": "awaiting_issue_name"
    }
    
    await update.message.reply_text(
        f"Найдено {len(emails)} email\n\n"
        f"Введите имя для Issues (будет добавлен номер пачки)\n"
        f"Например: 'New action' или 'Задача'\n\n"
        f"Или отправьте 'default' для использования 'New action'\n\n"
        f"Размер пачки: {BATCH_SIZE} email в один Issue"
    )

@check_access
async def handle_issue_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка имени Issue и запуск обработки"""
    user_id = update.effective_user.id
    issue_name = update.message.text.strip()
    
    if user_id not in user_sessions:
        await update.message.reply_text("Сессия истекла. Отправьте email заново.")
        return
    
    if user_sessions[user_id].get("step") != "awaiting_issue_name":
        await update.message.reply_text("Неверный шаг. Отправьте email заново.")
        return
    
    if issue_name.lower() == 'default':
        issue_name = "New action"
    
    emails = user_sessions[user_id]["emails"]
    total_batches = len(emails) // BATCH_SIZE + (1 if len(emails) % BATCH_SIZE else 0)
    
    progress_msg = await update.message.reply_text(
        f"Начинаю обработку...\n\n"
        f"Всего email: {len(emails)}\n"
        f"Имя Issue: {issue_name}\n"
        f"Размер пачки: {BATCH_SIZE}\n"
        f"Будет создано Issues: {total_batches}\n\n"
        f"Пожалуйста, подождите..."
    )
    
    results, total_success, total_errors = process_emails_in_batches(emails, BATCH_SIZE, issue_name)
    
    # Логируем использование
    db.log_usage(user_id, "process_emails", len(emails), len([r for r in results if r['status'] == 'success']))
    db.log_action(user_id, "process_emails", f"Обработано {len(emails)} email, создано {len([r for r in results if r['status'] == 'success'])} Issues")
    
    response = "ГОТОВО!\n\n"
    response += "СТАТИСТИКА:\n"
    response += f"  Успешно добавлено: {total_success}\n"
    response += f"  Ошибок: {total_errors}\n"
    response += f"  Создано Issues: {len([r for r in results if r['status'] == 'success'])}\n\n"
    
    response += "Созданные Issues:\n"
    for r in results:
        if r['status'] == 'success':
            response += f"  #{r['issue_id']}: {r['issue_title']}\n"
            response += f"  {r['issue_url']}\n\n"
    
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    try:
        await progress_msg.edit_text(response)
    except Exception:
        await progress_msg.delete()
        await update.message.reply_text(response)

@check_access
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений"""
    user_id = update.effective_user.id
    
    if user_id in user_sessions:
        step = user_sessions[user_id].get("step")
        
        if step == "editing_file":
            await save_file_edit(update, context)
            return
        elif step == "awaiting_issue_name":
            await handle_issue_name(update, context)
            return
    
    await handle_emails(update, context)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # Проверяем, является ли пользователь администратором
    if not db.is_admin(user_id):
        await query.edit_message_text("У вас нет прав администратора.")
        return
    
    if data == "admin_close":
        await query.edit_message_text("Админ-панель закрыта.")
        return
    
    elif data == "admin_users":
        users = db.get_all_users(active_only=True)
        if not users:
            await query.edit_message_text("Нет активных пользователей.")
            return
        
        text = "СПИСОК ПОЛЬЗОВАТЕЛЕЙ:\n\n"
        for user in users[:15]:
            user_id_db, username, first_name, last_name, is_admin, reg_date, last_active = user
            status = "АДМИН" if is_admin else "ПОЛЬЗОВАТЕЛЬ"
            name = first_name or username or str(user_id_db)
            text += f"ID: {user_id_db}\n"
            text += f"Имя: {name}\n"
            text += f"Статус: {status}\n"
            text += f"Регистрация: {reg_date[:16]}\n"
            text += f"Активность: {last_active[:16]}\n"
            text += "-" * 30 + "\n"
        
        if len(users) > 15:
            text += f"\n... и еще {len(users) - 15} пользователей"
        
        await query.edit_message_text(text)
    
    elif data == "admin_stats":
        stats = db.get_stats()
        text = (
            "СТАТИСТИКА БОТА\n\n"
            f"Всего пользователей: {stats['total_users']}\n"
            f"Администраторов: {stats['total_admins']}\n"
            f"Всего команд: {stats['total_commands']}\n"
            f"Обработано email: {stats['total_emails']}\n"
            f"Создано Issues: {stats['total_issues']}\n"
        )
        await query.edit_message_text(text)
    
    elif data == "admin_add_user":
        await query.edit_message_text(
            "Чтобы добавить пользователя, используйте команду:\n"
            "/adduser <user_id>\n\n"
            "Пример: /adduser 123456789"
        )
    
    elif data == "admin_remove_user":
        await query.edit_message_text(
            "Чтобы удалить пользователя, используйте команду:\n"
            "/removeuser <user_id>\n\n"
            "Пример: /removeuser 123456789"
        )
    
    elif data == "admin_logs":
        await query.edit_message_text(
            "Логи действий можно посмотреть в файле bot_database.db\n"
            "или через команду /logs (в разработке)"
        )
    
    else:
        await query.edit_message_text("Неизвестная команда.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже."
        )

def main():
    """Запуск бота"""
    print("=" * 60)
    print("  TELEGRAM GITLAB BOT")
    print("=" * 60)
    print(f"  GitLab: {GITLAB_URL}")
    print(f"  Project: {PROJECT_ID}")
    print(f"  File: {FILE_PATH}")
    print(f"  Branch: {BRANCH}")
    print(f"  Batch Size: {BATCH_SIZE}")
    print(f"  Admin ID: {ADMIN_IDS[0]}")
    print("=" * 60)
    print()
    print("Бот запущен!")
    print("   Команды:")
    print("   /start - Главное меню")
    print("   /help - Помощь")
    print("   /file - Показать содержимое файла")
    print("   /edit - Редактировать файл")
    print("   /admin - Админ-панель")
    print("   /users - Список пользователей")
    print("   /stats - Статистика")
    print("   /adduser - Добавить пользователя")
    print("   /removeuser - Удалить пользователя")
    print("=" * 60)
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("file", show_file))
    application.add_handler(CommandHandler("edit", edit_file_start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("adduser", add_user_command))
    application.add_handler(CommandHandler("removeuser", remove_user_command))
    application.add_handler(CommandHandler("cancel", cancel))
    
    # Обработчики
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nБот остановлен")
        sys.exit(0)
    except Exception as e:
        print(f"\nОшибка: {e}")
        input("Нажмите Enter для выхода...")
