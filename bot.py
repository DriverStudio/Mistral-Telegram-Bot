import telebot
from telebot import types, apihelper
from mistralai import Mistral
import sys
import os
import time
import datetime
import json
import urllib.parse
import requests
import threading # 🆕 Нужно для фонового сохранения
from keep_alive import keep_alive
import copy # 👈 ДОБАВИТЬ ВОТ ЭТО

keep_alive()

# ================= НАСТРОЙКИ =================
TELEGRAM_TOKEN = "8187242255:AAHIC-Kc06gyCEiTQWEr8i2bMOFr9bP8Wjc"
MISTRAL_API_KEY = "EE7AYZe6GjgDmrN6XxwvomRT9FH38Ysx"
ADMIN_IDS = [1071764183] 
WEB_APP_URL = "https://driverstudio.github.io/LaTeX-Converter/"
JSONBIN_API_KEY = "$2a$10$nh1KvXZw8oEvpKcpwn5mcusg.GwIUHn.z/dXiwtZYad70w3k4Rgym"

# 👇 ВСТАВЬТЕ СЮДА ВАШ BIN_ID (из админки -> Dashboard)
MAIN_BIN_ID = "ВСТАВИТЬ_СЮДА_ВАШ_ID_БИНА" 

# Как часто сохранять в облако (в секундах). 
# 600 сек = 10 минут. Час (3600) рискованно для бесплатного хостинга.
SAVE_INTERVAL = 600 
# =============================================

client = Mistral(api_key=MISTRAL_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

MODELS = ["mistral-large-latest", "pixtral-12b-2409", "ministral-8b-latest"]
current_model_index = 0
BOT_START_TIME = time.time()
TOTAL_MESSAGES = 0
TOTAL_ERRORS = 0

# --- ОБЛАЧНАЯ БАЗА ДАННЫХ (Фоновая) ---

def load_users_from_cloud():
    print("☁️ Загружаю базу из облака...")
    url = f'https://api.jsonbin.io/v3/b/{MAIN_BIN_ID}/latest'
    headers = {'X-Master-Key': JSONBIN_API_KEY}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()['record']
            # Превращаем ключи обратно в int (json хранит их как строки)
            return {int(k): v for k, v in data.items()}
    except Exception as e:
        print(f"⚠️ Ошибка загрузки (начнем с пустой): {e}")
    return {}

def save_users_to_cloud():
    """Эта функция будет вызываться по таймеру"""
    print("☁️ Фоновое сохранение базы...")
    url = f'https://api.jsonbin.io/v3/b/{MAIN_BIN_ID}'
    headers = {
        'Content-Type': 'application/json',
        'X-Master-Key': JSONBIN_API_KEY,
        'X-Bin-Versioning': 'false' # Не создаем кучу версий, просто обновляем
    }
    try:
        requests.put(url, json=user_histories, headers=headers)
        print("✅ База сохранена.")
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}")

# Фоновый процесс (Демон)
def background_saver():
    while True:
        time.sleep(SAVE_INTERVAL)
        save_users_to_cloud()

# 1. Загружаем базу при старте
user_histories = load_users_from_cloud()

# 2. Запускаем таймер сохранения в отдельном потоке
# daemon=True означает, что поток умрет сам, если основной бот упадет
saver_thread = threading.Thread(target=background_saver, daemon=True)
saver_thread.start()

# Заглушка для совместимости со старым кодом (чтобы не переписывать весь файл)
# Теперь эта функция ничего не делает, так как сохраняет фоновый поток
def save_users(): 
    pass

print("==========================================")
print(f"✨ Бот запущен. Юзеров в базе: {len(user_histories)}")
print("==========================================")

# --- ФУНКЦИИ ---

def get_current_model(): return MODELS[current_model_index]
def switch_to_next_model():
    global current_model_index
    current_model_index = (current_model_index + 1) % len(MODELS)
    return MODELS[current_model_index]

def get_history(chat_id):
    if chat_id not in user_histories:
        # Создаем структуру с полем saved_chats
        user_histories[chat_id] = {
            "name": "Unknown", 
            "history": [], 
            "saved_chats": {} # 🆕 Тут храним архивы: {"Название": [сообщения]}
        }
    # На всякий случай добавляем поле, если юзер старый
    if "saved_chats" not in user_histories[chat_id]:
        user_histories[chat_id]["saved_chats"] = {}
        
    return user_histories[chat_id]["history"]

def update_user_meta(message):
    chat_id = message.chat.id
    first = message.from_user.first_name or ""
    last = message.from_user.last_name or ""
    name = f"{first} {last}".strip() or f"User {chat_id}"
    
    if chat_id not in user_histories:
        user_histories[chat_id] = {"name": name, "history": [], "saved_chats": {}}
    else:
        user_histories[chat_id]["name"] = name
    # save_users() -> Теперь у нас автосохранение, эту строку можно убрать

# --- БЕЗОПАСНАЯ ОТПРАВКА ---

def safe_send_message(chat_id, text, reply_markup=None):
    try:
        bot.send_message(chat_id, text.replace('**', '*'), parse_mode='Markdown', reply_markup=reply_markup)
    except:
        try: bot.send_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
        except: pass

def safe_edit_message(chat_id, message_id, text, reply_markup=None):
    # Если текст пустой или None
    if not text: return

    # Если текст влезает в лимит Телеграма (4096), отправляем как обычно
    if len(text) < 4090:
        try:
            bot.edit_message_text(text.replace('**', '*'), chat_id, message_id, parse_mode='Markdown', reply_markup=reply_markup)
        except Exception as e:
            if "message is not modified" in str(e): return
            try: 
                # Если ошибка Markdown, шлем без форматирования
                bot.edit_message_text(text, chat_id, message_id, parse_mode=None, reply_markup=reply_markup)
            except Exception as e:
                # Если совсем всё плохо (например, сообщение слишком длинное), пишем ошибку
                print(f"Ошибка отправки: {e}")
                try: bot.edit_message_text(f"⚠️ Ошибка отображения: {e}", chat_id, message_id)
                except: pass
    else:
        # === ЛОГИКА ДЛЯ ДЛИННЫХ СООБЩЕНИЙ ===
        # Если ответ длинный, мы разбиваем его
        parts = []
        while len(text) > 0:
            if len(text) > 4090:
                # Ищем перенос строки, чтобы красиво разорвать
                part = text[:4090]
                last_newline = part.rfind('\n')
                if last_newline != -1:
                    parts.append(text[:last_newline])
                    text = text[last_newline+1:]
                else:
                    parts.append(text[:4090])
                    text = text[4090:]
            else:
                parts.append(text)
                text = ""

        # Редактируем "часики" на первую часть
        try:
            bot.edit_message_text(parts[0].replace('**', '*'), chat_id, message_id, parse_mode='Markdown')
        except:
            bot.edit_message_text(parts[0], chat_id, message_id)
        
        # Остальные части шлем новыми сообщениями
        for p in parts[1:]:
            time.sleep(0.3) # Маленькая пауза, чтобы не спамить
            try:
                bot.send_message(chat_id, p.replace('**', '*'), parse_mode='Markdown')
            except:
                bot.send_message(chat_id, p)

# --- УПРАВЛЕНИЕ СЕССИЯМИ ---

def save_current_session(chat_id, name):
    h = user_histories[chat_id]["history"]
    if not h: return False
    # Сохраняем текущую историю в словарь saved_chats
    user_histories[chat_id]["saved_chats"][name] = h
    return True

def load_session(chat_id, name):
    # Проверяем, есть ли вообще такой чат
    if name in user_histories[chat_id]["saved_chats"]:
        saved_data = user_histories[chat_id]["saved_chats"][name]
        
        # Если сохраненный чат пуст — нет смысла грузить
        if not saved_data:
            return False
            
        # ⚠️ ВАЖНО: Делаем полную копию данных (Deep Copy)
        # Это создает новый список в памяти, отвязывая его от архива
        user_histories[chat_id]["history"] = copy.deepcopy(saved_data)
        
        print(f"♻️ Восстановлен чат '{name}': {len(saved_data)} сообщений.")
        return True
    return False

def get_sessions_kb(chat_id):
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("💾 Сохранить текущий чат", callback_data="sess_save"))
    mk.add(types.InlineKeyboardButton("➕ Начать новый (с чистого листа)", callback_data="sess_new"))
    mk.add(types.InlineKeyboardButton("📂 Открыть все чаты (Web App)", callback_data="sess_open_web"))
    
    # ❌ УБРАНО: Генерация списка кнопок в чате
    # ❌ УБРАНА: Неработающая кнопка "--- ВАШИ ЧАТЫ ---"
    
    return mk

# --- ОБЛАКО ---

def save_answer_to_cloud(chat_id, query_text, answer_text):
    url = 'https://api.jsonbin.io/v3/b'
    headers = {'Content-Type': 'application/json', 'X-Master-Key': JSONBIN_API_KEY, 'X-Bin-Private': 'false'}
    payload = { "user_id": chat_id, "timestamp": str(datetime.datetime.now()), "model": get_current_model(), "query": query_text, "answer": answer_text }
    try: return requests.post(url, json=payload, headers=headers).json()['metadata']['id']
    except: return None


def save_full_db_to_cloud():
    print("📤 Подготовка данных для Дашборда...")
    url = 'https://api.jsonbin.io/v3/b'
    headers = {
        'Content-Type': 'application/json',
        'X-Master-Key': JSONBIN_API_KEY,
        'X-Bin-Private': 'false' # ⚠️ ВАЖНО: Копия должна быть публичной, чтобы сайт её открыл
    }
    
    try:
        # Принудительно превращаем ключи пользователей в строки для JSON
        # (в новой базе они int, а сайт может ждать string-ключи)
        clean_data = {str(k): v for k, v in user_histories.items()}
        
        req = requests.post(url, json=clean_data, headers=headers)
        if req.status_code == 200:
            bid = req.json()['metadata']['id']
            print(f"✅ Дашборд выгружен: {bid}")
            return bid
        else:
            print(f"❌ Ошибка JSONBin: {req.text}")
            return None
    except Exception as e:
        print(f"❌ Ошибка выгрузки: {e}")
        return None

def save_personal_history_to_cloud(user_id):
    url = 'https://api.jsonbin.io/v3/b'
    headers = {
        'Content-Type': 'application/json',
        'X-Master-Key': JSONBIN_API_KEY,
        'X-Bin-Private': 'false' # Должен быть публичным для WebApp
    }
    
    # Берем данные только ОДНОГО пользователя и оборачиваем в словарь с его ID
    # Это нужно, чтобы index.html понял формат (он ждет структуру {id: data})
    user_data = {str(user_id): user_histories.get(user_id, {})}
    
    try:
        req = requests.post(url, json=user_data, headers=headers)
        if req.status_code == 200:
            return req.json()['metadata']['id']
    except: pass
    return None

def ask_mistral_with_retry(chat_id, messages):
    global TOTAL_ERRORS
    for i in range(len(MODELS)):
        m = get_current_model()
        try:
            return client.chat.complete(model=m, messages=messages).choices[0].message.content
        except Exception as e:
            if "429" in str(e) or "400" in str(e):
                bot.send_message(chat_id, f"⚠️ {m} перегружена. Переключаюсь...", parse_mode='Markdown')
                switch_to_next_model()
                continue
            TOTAL_ERRORS += 1; raise e
    raise Exception("Все модели недоступны.")

# --- КЛАВИАТУРЫ ---

def get_main_kb(uid):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True)
    # 🆕 Добавили кнопку "🗃 Чаты"
    mk.add(types.KeyboardButton("∫ Редактор", web_app=types.WebAppInfo(url=WEB_APP_URL)), 
           types.KeyboardButton("🗃 Чаты")) 
    mk.add(types.KeyboardButton("🧹 Сброс контекста")) # Переименовал для ясности
    if uid in ADMIN_IDS: mk.add(types.KeyboardButton("🛠 Админка"))
    return mk

def get_admin_kb():
    mk = types.InlineKeyboardMarkup(row_width=2)
    c = get_current_model()
    mk.row(types.InlineKeyboardButton("✅ Lrg" if "large" in c else "🧠 Lrg", callback_data="set_model_0"),
           types.InlineKeyboardButton("✅ Pix" if "pixtral" in c else "👁 Pix", callback_data="set_model_1"),
           types.InlineKeyboardButton("✅ Min" if "mini" in c else "⚡ Min", callback_data="set_model_2"))
    
    # Кнопки управления
    mk.add(types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
           types.InlineKeyboardButton("👑 Dashboard", callback_data="admin_dashboard"))
           
    # НОВАЯ КНОПКА РАССЫЛКИ
    mk.add(types.InlineKeyboardButton("📢 Рассылка (Обновление)", callback_data="admin_broadcast"))

    mk.add(types.InlineKeyboardButton("🔄 Рестарт", callback_data="admin_restart"),
           types.InlineKeyboardButton("🛑 Стоп", callback_data="admin_stop"))
    mk.add(types.InlineKeyboardButton("❌ Закрыть", callback_data="admin_close"))
    return mk

# --- АДМИНКА (CALLBACKS) ---

@bot.callback_query_handler(func=lambda c: c.data.startswith('admin_') and c.from_user.id in ADMIN_IDS)
def admin_cb(c):
    global current_model_index
    
    if c.data == "admin_dashboard":
        bot.answer_callback_query(c.id, "Выгрузка...")
        bid = save_full_db_to_cloud()
        if bid:
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("🚀 Открыть панель", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?adminBinId={bid}")))
            bot.send_message(c.message.chat.id, "✅ База готова:", reply_markup=mk)
        else: bot.send_message(c.message.chat.id, "❌ Ошибка выгрузки")
    
    elif c.data == "admin_broadcast":
        bot.answer_callback_query(c.id, "Рассылка...")
        bot.send_message(c.message.chat.id, f"⏳ Начинаю рассылку для {len(user_histories)} пользователей...")
        count = 0
        for uid in user_histories:
            try:
                bot.send_message(uid, "♻️ Бот обновлен! Нажмите кнопку ниже, чтобы обновить меню.", reply_markup=get_main_kb(uid))
                count += 1
                time.sleep(0.05)
            except: pass
        bot.send_message(c.message.chat.id, f"✅ Рассылка завершена. Доставлено: {count}")

    elif c.data.startswith("set_model_"):
        current_model_index = int(c.data.split("_")[-1])
        try: bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=get_admin_kb())
        except: pass

    elif c.data == "admin_stats":
        up = str(datetime.timedelta(seconds=int(time.time()-BOT_START_TIME)))
        safe_edit_message(c.message.chat.id, c.message.message_id, f"📊 **Стат:**\n⏱ {up}\n✉️ {TOTAL_MESSAGES}\n👥 {len(user_histories)}", reply_markup=get_admin_inline_keyboard())

    elif c.data == "admin_restart":
        bot.answer_callback_query(c.id, "Перезагрузка...")
        os.execl(sys.executable, sys.executable, *sys.argv)

    elif c.data == "admin_stop":
        bot.delete_message(c.message.chat.id, c.message.message_id)
        print("🛑 ВЫКЛЮЧЕНИЕ ПО КОМАНДЕ АДМИНА")
        bot.stop_bot() # Останавливаем библиотеку
        os._exit(0)    # Жестко убиваем процесс (никаких ошибок в консоли не будет)

    elif c.data == "admin_close":
        bot.delete_message(c.message.chat.id, c.message.message_id)

# --- WEB APP ---

@bot.message_handler(content_types=['web_app_data'])
def web_data(m):
    print(f"DEBUG: Пришли данные WebApp! RAW: {m.web_app_data.data}")
    update_user_meta(m)
    cid = m.chat.id
    
    full_request = ""
    is_command = False # Флаг, чтобы понять, команда это или промпт

    try:
        # Пытаемся разобрать JSON от сайта
        d = json.loads(m.web_app_data.data)
        
        # === 1. ПРОВЕРКА НА КОМАНДУ ЗАГРУЗКИ ЧАТА (НОВОЕ) ===
        # === 1. ПРОВЕРКА НА КОМАНДУ ЗАГРУЗКИ ЧАТА ===
        if d.get('action') == 'load_session':
            session_name = d.get('name')
            
            if load_session(cid, session_name):
                # Считаем, сколько сообщений вспомнили
                msg_count = len(user_histories[cid]["history"])
                
                # Принудительно сохраняем в облако ПРЯМО СЕЙЧАС
                save_users_to_cloud()
                
                # Пишем пользователю подробный отчет
                safe_send_message(
                    cid, 
                    f"📂 **Чат «{session_name}» загружен!**\n"
                    f"🧠 Восстановлено сообщений: {msg_count}\n"
                    f"Контекст активен. Можете продолжать тему.", 
                    reply_markup=get_sessions_kb(cid)
                )
            else:
                safe_send_message(cid, "❌ Ошибка: Чат пуст или не найден.", reply_markup=get_sessions_kb(cid))
            
            return # 🛑 ВАЖНО: Выходим из функции, не отправляем это в нейросеть
            
        # === 2. ЕСЛИ ЭТО ОБЫЧНЫЙ ЗАПРОС ===
        full_request = d.get('full_text') or f"{d.get('text','')} $${d.get('formula','')}$$"

    except:
        # Если пришел не JSON, а обычный текст
        full_request = m.web_app_data.data

    # Если мы дошли сюда, значит это НЕ команда загрузки, а запрос к ИИ
    
    bot.send_message(cid, f"📥 **Запрос:**\n{full_request}", parse_mode=None)
    
    h = get_history(cid)
    h.append({"role": "user", "content": f"""
    Пользователь прислал математический запрос.
    Текст запроса:
    {full_request}
    
    Правила ответа:
    1. Решай структурно, не расписывай самые банальные вещи.
    2. Используй LaTeX для всех формул.
    3. Ответ не больше 3000 символов.

    ОЧЕНЬ ВАЖНОЕ ПРАВИЛО ФОРМАТИРОВАНИЯ:
    1. Для формул внутри строки используй ОДИНАРНЫЙ знак доллара. Пример: $E=mc^2$
    2. Для формул на отдельной строке используй ДВОЙНОЙ знак доллара. Пример: $$ \\int x dx $$
    3. НЕ используй квадратные скобки [ ] или ( ) для формул, только доллары!
    """})
    
    bot.send_chat_action(cid, 'typing')
    try:
        ans = ask_mistral_with_retry(cid, h)
        h.append({"role": "assistant", "content": ans})
        
        url = f"{WEB_APP_URL}?data={urllib.parse.quote(ans)}"
        mk = types.InlineKeyboardMarkup()
        
        if len(url) <= 2000:
            mk.add(types.InlineKeyboardButton("👀 Смотреть решение", web_app=types.WebAppInfo(url=url)))
            safe_send_message(cid, "✅ Решение готово:", reply_markup=mk)
        else:
            bid = save_answer_to_cloud(cid, full_request, ans)
            if bid:
                mk.add(types.InlineKeyboardButton("👀 Смотреть (Cloud)", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?binId={bid}")))
                safe_send_message(cid, "✅ Решение (Cloud):", reply_markup=mk)
            else: safe_send_message(cid, "❌ Сбой облака. Текст:\n"+ans)
        
        # Используем новую облачную функцию сохранения
        save_users_to_cloud() 
        
    except Exception as e: safe_send_message(cid, f"❌ Ошибка: {e}")

# --- ТЕКСТ ---

@bot.message_handler(commands=['start', 'reset'])
def start(m):
    update_user_meta(m)
    user_histories[m.chat.id]["history"] = []
    save_users()
    bot.send_message(m.chat.id, f"👋 Mistral ({get_current_model()})", reply_markup=get_main_kb(m.from_user.id))

@bot.message_handler(func=lambda m: m.text=="🛠 Админка" and m.from_user.id in ADMIN_IDS)
def adm(m): bot.send_message(m.chat.id, "⚙️ Панель:", reply_markup=get_admin_kb())

@bot.message_handler(func=lambda m: m.text=="🧹 Сброс")
def clr(m): 
    if m.chat.id in user_histories: user_histories[m.chat.id]["history"]=[]
    save_users()
    bot.send_message(m.chat.id, "🧠 Очищено")

# Обработчик кнопки "🗃 Чаты"
@bot.message_handler(func=lambda m: m.text == "🗃 Чаты")
def sessions_menu(m):
    msg = "🗂 **Управление диалогами**\n\nЗдесь вы можете сохранить текущий разговор или вернуться к старой теме, чтобы бот вспомнил контекст."
    safe_send_message(m.chat.id, msg, reply_markup=get_sessions_kb(m.chat.id))

# Callback'и для кнопок меню чатов
@bot.callback_query_handler(func=lambda c: c.data.startswith("sess_"))
def session_callbacks(c):
    cid = c.message.chat.id
    action = c.data
    
    if action == "sess_save":
        msg = bot.send_message(cid, "✏️ Введите название для этого чата:", reply_markup=types.ForceReply())
        bot.register_next_step_handler(msg, process_save_name)
    
    elif action == "sess_new":
        # ❌ УБРАНО: Сохранение в "Auto..."
        
        # Просто очищаем историю
        user_histories[cid]["history"] = []
        bot.answer_callback_query(c.id, "Новый контекст создан!")
        
        safe_edit_message(cid, c.message.message_id, "✨ **Начат новый диалог.**\nКонтекст очищен. Можете начинать новую тему.", reply_markup=get_sessions_kb(cid))
        save_users_to_cloud() # Сохраняем очистку в облако
        
        user_histories[cid]["history"] = []
        bot.answer_callback_query(c.id, "Новый контекст создан!")
        safe_edit_message(cid, c.message.message_id, "✨ **Начат новый диалог.**\nКонтекст очищен. Старый сохранен в авто-сохранениях.", reply_markup=get_sessions_kb(cid))
        save_users_to_cloud() # Сразу в облако

    elif action.startswith("sess_load_"):
        name = action.replace("sess_load_", "")
        if load_session(cid, name):
            bot.answer_callback_query(c.id, f"Загружен: {name}")
            safe_edit_message(cid, c.message.message_id, f"📂 **Чат «{name}» восстановлен!**\nБот теперь помнит всё, что было в том разговоре.", reply_markup=get_sessions_kb(cid))
            save_users_to_cloud()
        else:
            bot.answer_callback_query(c.id, "Ошибка загрузки", show_alert=True)

    if action == "sess_open_web":
        bot.answer_callback_query(c.id, "Подготовка данных...")
        
        # 1. Загружаем данные юзера в облако
        bin_id = save_personal_history_to_cloud(cid)
        
        if bin_id:
            # 2. Создаем ссылку на Web App с параметром adminBinId
            # (Мы используем adminBinId, так как он заставляет сайт отрисовать интерфейс выбора чатов)
            web_url = f"{WEB_APP_URL}?adminBinId={bin_id}"
            
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("🚀 Открыть мои чаты", web_app=types.WebAppInfo(url=web_url)))
            
            # Возвращаем кнопку "Назад", чтобы меню не исчезало насовсем
            mk.add(types.InlineKeyboardButton("🔙 Меню", callback_data="sess_back"))
            
            safe_edit_message(cid, c.message.message_id, "✅ **Данные готовы!**\nНажмите кнопку ниже, чтобы управлять чатами в графическом интерфейсе.", reply_markup=mk)
        else:
            bot.answer_callback_query(c.id, "Ошибка облака ☁️", show_alert=True)

    elif action == "sess_back":
        # Кнопка возврата в обычное меню
        safe_edit_message(cid, c.message.message_id, "🗂 **Управление диалогами**", reply_markup=get_sessions_kb(cid))

def process_save_name(m):
    cid = m.chat.id
    name = m.text[:20] # Ограничим длину имени
    if save_current_session(cid, name):
        safe_send_message(cid, f"✅ Чат сохранен как **{name}**!", reply_markup=get_main_kb(cid))
        save_users_to_cloud()
    else:
        safe_send_message(cid, "❌ Чат пуст, сохранять нечего.")

@bot.message_handler(func=lambda m: True)
def txt(m):
    global TOTAL_MESSAGES
    TOTAL_MESSAGES += 1
    update_user_meta(m)
    cid = m.chat.id
    
    # Ставим часики
    w = bot.reply_to(m, "⏳")
    
    h = get_history(cid)
    h.append({"role": "user", "content": m.text})
    
    try:
        ans = ask_mistral_with_retry(cid, h)
        h.append({"role": "assistant", "content": ans})
        
        # Используем новую умную функцию отправки
        safe_edit_message(cid, w.message_id, ans)
        
        # (Если используете старое сохранение, оставьте, если новое облачное — оно само сохранится)
        save_users() 
        
    except Exception as e:
        print(f"Handler Error: {e}")
        # 👇 ТЕПЕРЬ БОТ СКАЖЕТ ВАМ, В ЧЕМ ОШИБКА, ВМЕСТО ВЕЧНЫХ ЧАСИКОВ
        try:
            bot.edit_message_text(f"❌ Сбой: {e}", cid, w.message_id)
        except: pass

if __name__ == '__main__':
    print("🚀 Бот запущен...")
    # Цикл перезапуска на случай падений (сеть и т.д.)
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"CRASH: {e}")
            time.sleep(5)
