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
# Telegram Bot Token
TELEGRAM_TOKEN = "8940213656:AAEHr3mf-9tmrPNHs56gu4e8T90ojpps85A"

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
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                gitlab_url TEXT DEFAULT 'https://gitlab.com',
                private_token TEXT,
                project_id TEXT,
                file_path TEXT DEFAULT '.gitlab/service_desk_templates/new_participant.md',
                branch TEXT DEFAULT 'main',
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
    
    def get_user_settings(self, user_id):
        """Получить настройки пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT gitlab_url, private_token, project_id, file_path, branch
            FROM users WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'gitlab_url': result[0] or 'https://gitlab.com',
                'private_token': result[1],
                'project_id': result[2],
                'file_path': result[3] or '.gitlab/service_desk_templates/new_participant.md',
                'branch': result[4] or 'main'
            }
        return None
    
    def update_user_settings(self, user_id, gitlab_url=None, private_token=None, 
                            project_id=None, file_path=None, branch=None):
        """Обновить настройки пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            updates = []
            params = []
            
            if gitlab_url is not None:
                updates.append("gitlab_url = ?")
                params.append(gitlab_url)
            if private_token is not None:
                updates.append("private_token = ?")
                params.append(private_token)
            if project_id is not None:
                updates.append("project_id = ?")
                params.append(project_id)
            if file_path is not None:
                updates.append("file_path = ?")
                params.append(file_path)
            if branch is not None:
                updates.append("branch = ?")
                params.append(branch)
            
            if updates:
                params.append(user_id)
                query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
                cursor.execute(query, params)
                conn.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка обновления настроек: {e}")
            return False
        finally:
            conn.close()
    
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
    
    def get_stats(self, user_id=None):
        """Получить статистику"""
        conn = self.get_connection()
        cursor = conn.cursor()
        stats = {}
        
        if user_id:
            # Статистика конкретного пользователя
            cursor.execute('SELECT COUNT(*) FROM users WHERE user_id = ? AND is_active = 1', (user_id,))
            stats['is_active'] = cursor.fetchone()[0] > 0
            
            cursor.execute('SELECT COUNT(*) FROM usage_stats WHERE user_id = ?', (user_id,))
            stats['total_commands'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT SUM(emails_count) FROM usage_stats WHERE user_id = ?', (user_id,))
            total_emails = cursor.fetchone()[0]
            stats['total_emails'] = total_emails if total_emails else 0
            
            cursor.execute('SELECT SUM(issues_created) FROM usage_stats WHERE user_id = ?', (user_id,))
            total_issues = cursor.fetchone()[0]
            stats['total_issues'] = total_issues if total_issues else 0
        else:
            # Общая статистика
            cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
            stats['total_users'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1')
            stats['total_admins'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM usage_stats')
            stats['total_commands'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT SUM(emails_count) FROM usage_stats')
            total_emails = cursor.fetchone()[0]
            stats['total_emails'] = total_emails if total_emails else 0
            
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

def create_issue(user_id, title, description=""):
    """Создание нового Issue с настройками пользователя"""
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        return False, None, "Настройки GitLab не заполнены. Используйте /settings"
    
    url = f"{settings['gitlab_url']}/api/v4/projects/{settings['project_id']}/issues"
    headers = {
        "PRIVATE-TOKEN": settings['private_token'],
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
        logger.error(f"Ошибка создания Issue: {e}")
        return False, None, str(e)

def add_single_email_to_issue(user_id, issue_id, email, delay=0.3):
    """Добавление одного email через команду /add_email"""
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        return False, "Настройки GitLab не заполнены"
    
    comment = f"/add_email {email}"
    
    url = f"{settings['gitlab_url']}/api/v4/projects/{settings['project_id']}/issues/{issue_id}/notes"
    headers = {
        "PRIVATE-TOKEN": settings['private_token'],
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

def process_emails_in_batches(user_id, emails, batch_size=BATCH_SIZE, issue_name="New action"):
    """Обработка email пачками с кастомным именем Issue"""
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        return [], 0, 0, "Настройки GitLab не заполнены. Используйте /settings"
    
    total_emails = len(emails)
    batches = [emails[i:i+batch_size] for i in range(0, total_emails, batch_size)]
    
    results = []
    total_success = 0
    total_errors = 0
    
    for batch_num, batch in enumerate(batches, 1):
        issue_title = f"{issue_name} #{batch_num}"
        
        success, issue_id, issue_url = create_issue(user_id, issue_title)
        
        if not success:
            results.append({
                "batch": batch_num,
                "status": "error",
                "message": issue_url
            })
            continue
        
        batch_results = []
        for email in batch:
            success, message = add_single_email_to_issue(user_id, issue_id, email)
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
    
    return results, total_success, total_errors, None

def encode_file_path(file_path):
    """Кодирование пути файла для URL"""
    return quote(file_path, safe='')

def get_file_content(user_id, file_path=None, branch=None):
    """Получить содержимое файла из репозитория"""
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        return None, "Настройки GitLab не заполнены. Используйте /settings"
    
    file_path = file_path or settings['file_path']
    branch = branch or settings['branch']
    
    encoded_path = encode_file_path(file_path)
    url = f"{settings['gitlab_url']}/api/v4/projects/{settings['project_id']}/repository/files/{encoded_path}"
    params = {
        "ref": branch,
        "private_token": settings['private_token']
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

def update_file_content(user_id, new_content, file_path=None, branch=None, commit_message="Обновление файла через Telegram бота"):
    """Обновить содержимое файла в репозитории"""
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        return False, "Настройки GitLab не заполнены. Используйте /settings"
    
    file_path = file_path or settings['file_path']
    branch = branch or settings['branch']
    
    encoded_path = encode_file_path(file_path)
    url = f"{settings['gitlab_url']}/api/v4/projects/{settings['project_id']}/repository/files/{encoded_path}"
    headers = {
        "PRIVATE-TOKEN": settings['private_token'],
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
    
    settings = db.get_user_settings(user_id)
    has_settings = settings and settings['private_token'] and settings['project_id']
    
    welcome_text = (
        "🤖 GitLab Issue Creator Bot\n\n"
        "Я помогу вам создавать Issues в GitLab и управлять файлами.\n\n"
        f"📊 Статус настроек: {'✅ Настроен' if has_settings else '❌ Требуется настройка'}\n"
    )
    
    if has_settings:
        welcome_text += (
            f"🔗 GitLab: {settings['gitlab_url']}\n"
            f"📁 Проект: {settings['project_id']}\n"
            f"📄 Файл: {settings['file_path']}\n"
        )
    
    welcome_text += (
        "\n📌 Команды:\n"
        "/start - Показать это сообщение\n"
        "/help - Помощь\n"
        "/settings - Настроить GitLab\n"
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

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка GitLab"""
    user_id = update.effective_user.id
    
    if not db.is_user_allowed(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return
    
    settings = db.get_user_settings(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🔧 GitLab URL", callback_data="settings_url")],
        [InlineKeyboardButton("🔑 Private Token", callback_data="settings_token")],
        [InlineKeyboardButton("📁 Project ID", callback_data="settings_project")],
        [InlineKeyboardButton("📄 File Path", callback_data="settings_file")],
        [InlineKeyboardButton("🌿 Branch", callback_data="settings_branch")],
        [InlineKeyboardButton("📊 Показать настройки", callback_data="settings_show")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="settings_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "⚙️ НАСТРОЙКИ GITLAB\n\n"
    if settings:
        text += f"🔗 URL: {settings['gitlab_url']}\n"
        text += f"🔑 Token: {'✅ Установлен' if settings['private_token'] else '❌ Не установлен'}\n"
        text += f"📁 Project ID: {settings['project_id'] or '❌ Не установлен'}\n"
        text += f"📄 File Path: {settings['file_path']}\n"
        text += f"🌿 Branch: {settings['branch']}\n"
    else:
        text += "Настройки не найдены.\n"
    
    text += "\nВыберите параметр для настройки:"
    
    await update.message.reply_text(text, reply_markup=reply_markup)

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback кнопок настроек"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "settings_close":
        await query.edit_message_text("Настройки закрыты.")
        return
    
    elif data == "settings_show":
        settings = db.get_user_settings(user_id)
        text = "📊 ТЕКУЩИЕ НАСТРОЙКИ:\n\n"
        if settings:
            text += f"🔗 GitLab URL: {settings['gitlab_url']}\n"
            text += f"🔑 Private Token: {'✅ Установлен' if settings['private_token'] else '❌ Не установлен'}\n"
            text += f"📁 Project ID: {settings['project_id'] or '❌ Не установлен'}\n"
            text += f"📄 File Path: {settings['file_path']}\n"
            text += f"🌿 Branch: {settings['branch']}\n"
        else:
            text += "Настройки не найдены."
        await query.edit_message_text(text)
        return
    
    # Сохраняем в сессию какой параметр настраиваем
    user_sessions[user_id] = {
        "step": "settings_input",
        "setting": data
    }
    
    setting_names = {
        "settings_url": "GitLab URL",
        "settings_token": "Private Token",
        "settings_project": "Project ID",
        "settings_file": "File Path",
        "settings_branch": "Branch"
    }
    
    await query.edit_message_text(
        f"Введите новый {setting_names.get(data, 'параметр')}:\n\n"
        "Отправьте значение в следующем сообщении.\n"
        "Для отмены используйте /cancel"
    )

async def handle_settings_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода настроек"""
    user_id = update.effective_user.id
    value = update.message.text.strip()
    
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "settings_input":
        return
    
    setting = user_sessions[user_id].get("setting")
    
    # Обновляем настройку
    updates = {}
    if setting == "settings_url":
        updates['gitlab_url'] = value
    elif setting == "settings_token":
        updates['private_token'] = value
    elif setting == "settings_project":
        updates['project_id'] = value
    elif setting == "settings_file":
        updates['file_path'] = value
    elif setting == "settings_branch":
        updates['branch'] = value
    
    if db.update_user_settings(user_id, **updates):
        db.log_action(user_id, "settings_update", f"Обновлен параметр {setting}")
        await update.message.reply_text(f"✅ Настройка успешно обновлена!")
    else:
        await update.message.reply_text("❌ Ошибка при обновлении настройки.")
    
    # Удаляем сессию
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    # Показываем меню настроек
    await settings_command(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    user_id = update.effective_user.id
    
    if not db.is_user_allowed(user_id):
        await update.message.reply_text(
            "❌ У вас нет доступа к этому боту.\n\n"
            "Пожалуйста, обратитесь к администратору для получения доступа."
        )
        return
    
    db.log_action(user_id, "help", "Пользователь запросил помощь")
    
    help_text = (
        "📖 ПОМОЩЬ\n\n"
        "1. Настройка GitLab:\n"
        "   Используйте /settings для настройки вашего GitLab\n"
        "   Каждый пользователь использует свои токены и проекты\n\n"
        "2. Добавление пользователей:\n"
        "   Отправьте список email через запятую\n"
        "   Затем введите имя для Issues\n"
        "   Email будут автоматически распределены по 10 штук в один Issue\n\n"
        "3. Работа с файлом:\n"
        "   /file - показать текущее содержимое\n"
        "   /edit - начать редактирование файла\n\n"
        "⚙️ Команды:\n"
        "/start - Главное меню\n"
        "/help - Помощь\n"
        "/settings - Настроить GitLab\n"
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
    
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        await update.message.reply_text(
            "❌ Настройки GitLab не заполнены!\n"
            "Используйте /settings для настройки."
        )
        return
    
    await update.message.reply_text("Загружаю содержимое файла...")
    
    content, error = get_file_content(user_id)
    
    if error:
        if "не найден" in error:
            await update.message.reply_text(
                f"❌ Файл {settings['file_path']} не найден!\n\n"
                f"Проверьте:\n"
                f"1. Правильный ли путь к файлу\n"
                f"2. Существует ли ветка {settings['branch']}\n"
                f"3. Есть ли доступ к репозиторию\n\n"
                f"Используйте /settings для изменения настроек."
            )
        else:
            await update.message.reply_text(f"❌ Ошибка при чтении файла:\n\n{error}")
        return
    
    if len(content) > 4000:
        content = content[:4000] + "\n\n... (файл обрезан)"
    
    await update.message.reply_text(
        f"📄 Содержимое файла:\n"
        f"Файл: {settings['file_path']}\n\n"
        f"```\n{content}\n```",
        parse_mode='Markdown'
    )

@check_access
async def edit_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование файла"""
    user_id = update.effective_user.id
    
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        await update.message.reply_text(
            "❌ Настройки GitLab не заполнены!\n"
            "Используйте /settings для настройки."
        )
        return
    
    db.log_action(user_id, "edit_file_start", "Начало редактирования файла")
    
    await update.message.reply_text("Проверяю существование файла...")
    
    content, error = get_file_content(user_id)
    
    if error:
        if "не найден" in error:
            await update.message.reply_text(
                f"❌ Файл {settings['file_path']} не найден!\n\n"
                f"Создайте файл вручную в репозитории или измените настройки."
            )
            return
        else:
            await update.message.reply_text(f"❌ Ошибка: {error}")
            return
    
    user_sessions[user_id] = {
        "step": "editing_file",
        "original_content": content
    }
    
    await update.message.reply_text(
        f"📝 Редактирование файла {settings['file_path']}\n\n"
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
    
    success, error = update_file_content(user_id, new_content)
    
    if success:
        db.log_action(user_id, "save_file", "Файл успешно сохранен")
        await update.message.reply_text("✅ Файл успешно обновлен!")
    else:
        await update.message.reply_text(f"❌ Ошибка при сохранении:\n\n{error}")
    
    if user_id in user_sessions:
        del user_sessions[user_id]

@check_access
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cancel"""
    user_id = update.effective_user.id
    
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("❌ Операция отменена")
    else:
        await update.message.reply_text("Нет активной операции")

@check_access
async def handle_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученных email"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Проверяем, не вводят ли настройки
    if user_id in user_sessions and user_sessions[user_id].get("step") == "settings_input":
        await handle_settings_input(update, context)
        return
    
    if user_id in user_sessions and user_sessions[user_id].get("step") == "editing_file":
        await save_file_edit(update, context)
        return
    
    # Проверяем настройки
    settings = db.get_user_settings(user_id)
    if not settings or not settings['private_token'] or not settings['project_id']:
        await update.message.reply_text(
            "❌ Настройки GitLab не заполнены!\n"
            "Используйте /settings для настройки перед созданием Issues."
        )
        return
    
    emails = parse_emails(text)
    
    if not emails:
        await update.message.reply_text(
            "❌ Не найдено корректных email!\n"
            "Пожалуйста, отправьте email через запятую, пробел или новой строкой."
        )
        return
    
    user_sessions[user_id] = {
        "emails": emails,
        "step": "awaiting_issue_name"
    }
    
    await update.message.reply_text(
        f"📧 Найдено {len(emails)} email\n\n"
        f"Введите имя для Issues (будет добавлен номер пачки)\n"
        f"Например: 'New action' или 'Задача'\n\n"
        f"Или отправьте 'default' для использования 'New action'\n\n"
        f"📦 Размер пачки: {BATCH_SIZE} email в один Issue"
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
        f"🔄 Начинаю обработку...\n\n"
        f"Всего email: {len(emails)}\n"
        f"Имя Issue: {issue_name}\n"
        f"Размер пачки: {BATCH_SIZE}\n"
        f"Будет создано Issues: {total_batches}\n\n"
        f"Пожалуйста, подождите..."
    )
    
    results, total_success, total_errors, error = process_emails_in_batches(user_id, emails, BATCH_SIZE, issue_name)
    
    if error:
        await progress_msg.edit_text(f"❌ Ошибка: {error}")
        if user_id in user_sessions:
            del user_sessions[user_id]
        return
    
    # Логируем использование
    db.log_usage(user_id, "process_emails", len(emails), len([r for r in results if r['status'] == 'success']))
    db.log_action(user_id, "process_emails", f"Обработано {len(emails)} email, создано {len([r for r in results if r['status'] == 'success'])} Issues")
    
    response = "✅ ГОТОВО!\n\n"
    response += "📊 СТАТИСТИКА:\n"
    response += f"  ✅ Успешно добавлено: {total_success}\n"
    response += f"  ❌ Ошибок: {total_errors}\n"
    response += f"  📦 Создано Issues: {len([r for r in results if r['status'] == 'success'])}\n\n"
    
    response += "🔗 Созданные Issues:\n"
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
        
        if step == "settings_input":
            await handle_settings_input(update, context)
            return
        elif step == "editing_file":
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
    
    # Проверка настроек
    if data.startswith("settings_"):
        # Проверяем доступ
        if not db.is_user_allowed(user_id):
            await query.edit_message_text("❌ У вас нет доступа к этому боту.")
            return
        await settings_callback(update, context)
        return
    
    # Админ-панель
    if not db.is_admin(user_id):
        await query.edit_message_text("❌ У вас нет прав администратора.")
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
    print(f"  Bot Token: {TELEGRAM_TOKEN[:20]}...")
    print(f"  Batch Size: {BATCH_SIZE}")
    print(f"  Admin ID: {ADMIN_IDS[0]}")
    print("=" * 60)
    print()
    print("Бот запущен!")
    print("   Команды:")
    print("   /start - Главное меню")
    print("   /help - Помощь")
    print("   /settings - Настройки GitLab")
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
    application.add_handler(CommandHandler("settings", settings_command))
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
