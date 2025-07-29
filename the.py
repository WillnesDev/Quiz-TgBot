# telegram_quiz_bot.py
# Bu bot @Yunusbek0 tomonidan yaratilgan
import telebot
from telebot import types
import openpyxl
import sqlite3
import random
import csv
import os
from threading import Timer
import glob
# PDF va DOCX uchun kutubxonalar
import docx
import PyPDF2
import time
import json

# ================= Bot token ===================
BOT_TOKEN = "8053534225:AAGfzWhFc9ZJaMwxL9RM3tVZVae_hdPnCHM"
ADMIN_IDS = [5106477690, 7095273576]  # << ADMIN Telegram ID larini shu yerga yozing
bot = telebot.TeleBot(BOT_TOKEN)

# ================ SQLite =======================
def init_db():
    conn = sqlite3.connect("quiz.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS results (
            user_id INTEGER,
            full_name TEXT,
            phone_number TEXT,
            test_name TEXT,
            score INTEGER
        )
    ''')
    # Agar test_name ustuni yo'q bo'lsa, qo'shamiz
    try:
        c.execute("ALTER TABLE results ADD COLUMN test_name TEXT")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    conn.commit()
    conn.close()

def save_result(user_id, full_name, phone_number, score, test_name=None):
    if test_name is None:
        test_name = "default"
    conn = sqlite3.connect("quiz.db")
    c = conn.cursor()
    c.execute("INSERT INTO results (user_id, full_name, phone_number, test_name, score) VALUES (?, ?, ?, ?, ?)",
              (user_id, full_name, phone_number, test_name, score))
    conn.commit()
    conn.close()

def export_results_to_csv():
    conn = sqlite3.connect("quiz.db")
    c = conn.cursor()
    c.execute("SELECT full_name, phone_number, test_name, score FROM results")
    rows = c.fetchall()
    conn.close()

    with open("results.csv", "w", newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Ism", "Telefon", "Test nomi", "Testdagi ball"])
        writer.writerows(rows)

# ================ Exceldan savollar ==================
def load_questions_from_excel(file_path):
    workbook = openpyxl.load_workbook(file_path)
    sheet = workbook.active
    questions = []

    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 6 or any(cell is None for cell in row[:6]):
            continue  # bo'sh yoki noto'g'ri qatorlarni o'tkazib yuborish
        question_text, opt_a, opt_b, opt_c, opt_d, correct_letter = row
        options = [opt_a, opt_b, opt_c, opt_d]
        letter_to_index = {"A": 0, "B": 1, "C": 2, "D": 3}
        correct_index = letter_to_index.get(str(correct_letter).strip().upper(), 0)
        questions.append({
            "question": question_text,
            "options": options,
            "correct": correct_index
        })

    return questions

def load_questions_from_docx(file_path):
    doc = docx.Document(file_path)
    questions = []
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        question_text = lines[i]
        options = []
        for j in range(1, 5):
            if i + j < len(lines):
                options.append(lines[i + j][3:].strip() if len(lines[i + j]) > 2 else lines[i + j])
        correct_letter = None
        if i + 5 < len(lines) and lines[i + 5].lower().startswith('javob:'):
            correct_letter = lines[i + 5].split(':', 1)[1].strip().upper()
        letter_to_index = {"A": 0, "B": 1, "C": 2, "D": 3}
        correct_index = letter_to_index.get(str(correct_letter).strip().upper(), 0)
        if len(options) == 4:
            questions.append({
                "question": question_text,
                "options": options,
                "correct": correct_index
            })
        i += 6  # Savol + 4 variant + javob
    return questions

def load_questions_from_pdf(file_path):
    questions = []
    with open(file_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        question_text = lines[i]
        options = []
        for j in range(1, 5):
            if i + j < len(lines):
                options.append(lines[i + j][3:].strip() if len(lines[i + j]) > 2 else lines[i + j])
        correct_letter = None
        if i + 5 < len(lines) and lines[i + 5].lower().startswith('javob:'):
            correct_letter = lines[i + 5].split(':', 1)[1].strip().upper()
        letter_to_index = {"A": 0, "B": 1, "C": 2, "D": 3}
        correct_index = letter_to_index.get(str(correct_letter).strip().upper(), 0)
        if len(options) == 4:
            questions.append({
                "question": question_text,
                "options": options,
                "correct": correct_index
            })
        i += 6  # Savol + 4 variant + javob
    return questions

QUESTIONS = load_questions_from_excel("quiz.xlsx")

# ================= Session ma'lumotlari ================
user_data = {}

class UserSession:
    def __init__(self):
        self.score = 0
        self.current_question = 0
        self.is_active = False
        self.full_name = ""
        self.phone_number = ""
        self.questions = []
        self.wrong_answers = []
        self.timer = None
        self.verification_step = "name"  # name, phone, verify
        self.verification_code = None

def get_user_session(user_id):
    if user_id not in user_data:
        user_data[user_id] = UserSession()
    return user_data[user_id]

# ================= Verifikatsiya kod generatori ==============
VERIFICATION_FILE = "ver_codes.txt"

def generate_verification_code(user_id):
    code = str(random.randint(10000, 99999))
    
    # Faylga yozish
    with open(VERIFICATION_FILE, "a", encoding='utf-8') as f:
        f.write(f"{user_id}:{code}\n")
    
    return code

def verify_code(user_id, input_code):
    # Fayldan tekshirish
    try:
        with open(VERIFICATION_FILE, "r", encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()
                # Bo'sh satr va izohlarni o'tkazib yuborish
                if line and not line.startswith("#") and ":" in line:
                    try:
                        uid, code = line.split(":", 1)
                        if int(uid) == user_id and code == input_code:
                            return True
                    except (ValueError, IndexError):
                        continue
    except FileNotFoundError:
        pass
    return False

def clear_verification_code(user_id):
    """Verifikatsiya kodini fayldan o'chirish"""
    try:
        with open(VERIFICATION_FILE, "r", encoding='utf-8') as f:
            lines = f.readlines()
        
        with open(VERIFICATION_FILE, "w", encoding='utf-8') as f:
            for line in lines:
                line = line.strip()
                # Bo'sh satr, izoh va tegishli user_id ni o'tkazib yuborish
                if line and not line.startswith("#") and not line.startswith(f"{user_id}:"):
                    f.write(line + "\n")
    except FileNotFoundError:
        pass

# test_time_limits.json va unga oid kodlar olib tashlandi.

# ================== /start ============================
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    user_data[user_id] = UserSession()
    welcome_text = (
        "🤖 *Xush kelibsiz, Quiz botiga!*\n\n"
        "Quyidagi buyruqlar orqali botdan foydalanishingiz mumkin:\n\n"
        "/start - Ro'yxatdan o'tish va telefon tasdiqlash\n"
        "/quiz - Testni boshlash (faqat tasdiqlangan foydalanuvchilar)\n"
        "/help - Yordam\n"
        "/restart - Boshidan boshlash\n"
        "/admin - Faqat admin uchun menyu\n"
        "\n📱 *Telefon tasdiqlash jarayoni:*\n"
        "1. /start - ism va familiya kiriting\n"
        "2. Telefon raqamni yuboring\n"
        "3. Muvaffaqiyatli tasdiqlangandan so'ng /quiz - testni boshlang\n"
        "\n_Bu bot @Yunusbek0 tomonidan yaratilgan_"
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown')
    # /stop tugmasi bilan ism-familiya so'rash
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("/stop"))
    bot.send_message(message.chat.id, "Ismingiz va familiyangizni kiriting:", reply_markup=markup)
    bot.register_next_step_handler(message, ask_phone)

def ask_phone(message):
    user_id = message.from_user.id
    # Ism kiritishda / bilan boshlansa, xatolik va qayta so'rash
    if message.text.strip().startswith('/'):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(types.KeyboardButton("/stop"))
        bot.send_message(message.chat.id, "❌ Xato bo‘ldi, iltimos ismingizni kiriting:", reply_markup=markup)
        bot.register_next_step_handler(message, ask_phone)
        return
    session = get_user_session(user_id)
    session.full_name = message.text
    # /stop tugmasi bilan telefon raqami so'rash
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    button = types.KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)
    markup.add(button)
    markup.add(types.KeyboardButton("/stop"))
    bot.send_message(message.chat.id, "Telefon raqamingizni yuboring:", reply_markup=markup)

@bot.message_handler(content_types=['contact'])
def handle_phone_contact(message):
    user_id = message.from_user.id
    session = get_user_session(user_id)
    phone = message.contact.phone_number

    # Telefon raqamni to'g'ri formatga keltirish
    if phone.startswith("998"):
        phone = "+998" + phone[3:]
    elif not phone.startswith("+998"):
        bot.send_message(message.chat.id, "❌ Faqat O'zbekiston raqamlari qabul qilinadi.")
        return

    session.phone_number = phone
    session.verification_step = "completed"

    welcome = f"""✅ *Muvaffaqiyatli tasdiqlandi!*

👤 *Ism:* {session.full_name}
📞 *Tel:* {session.phone_number}

Testni boshlash uchun /quiz buyrug'ini yuboring."""
    bot.send_message(message.chat.id, welcome, parse_mode='Markdown')

# ================== /quiz ============================
@bot.message_handler(commands=['new'])
def new_test_command(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "Faqat admin yangi test yuklashi mumkin!")
        return
    bot.send_message(message.chat.id, "Yangi test faylini (Excel, .xlsx) yuboring.")
    bot.register_next_step_handler(message, handle_new_test_file)

def handle_new_test_file(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "Faqat admin yangi test yuklashi mumkin!")
        return
    if not message.document:
        bot.send_message(message.chat.id, "Fayl yuborilmadi. Iltimos, .xlsx fayl yuboring.")
        return
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name
    downloaded_file = bot.download_file(file_info.file_path)
    with open(file_name, "wb") as f:
        f.write(downloaded_file)
    questions = load_questions_from_excel(file_name)
    num_questions = len(questions)
    bot.send_message(message.chat.id, f"✅ Yangi test fayli yuklandi! ({file_name})\nSavollar soni: {num_questions}")

@bot.message_handler(commands=['quiz'])
def start_quiz(message):
    user_id = message.from_user.id
    test_files = [f for f in glob.glob("*.xlsx") if not f.startswith("~$")]
    if not test_files:
        bot.send_message(message.chat.id, "Hech qanday test fayli topilmadi.")
        return
    test_names = [f.replace(".xlsx", "") for f in test_files]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for name in test_names:
        markup.add(types.InlineKeyboardButton(name.title(), callback_data=f"select_test_{name}"))
    bot.send_message(message.chat.id, "Qaysi testni tanlaysiz?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_test_'))
def handle_select_test(call):
    user_id = call.from_user.id
    test_name = call.data.replace('select_test_', '')
    file_name = f"{test_name}.xlsx"
    questions = load_questions_from_excel(file_name)
    random.shuffle(questions)
    user_data[user_id] = {
        'questions': questions,
        'score': 0,
        'current_question': 0,
        'wrong_answers': [],
        'time_limit': get_test_time_limit(len(questions))
    }
    time_limit = user_data[user_id]['time_limit']
    if time_limit:
        def time_up():
            bot.send_message(call.message.chat.id, "⏰ Vaqt tugadi! Natijangiz quyidagicha:")
            show_results_simple(call.message.chat.id, user_data[user_id])
            user_data.pop(user_id, None)
        Timer(time_limit, time_up).start()
        bot.send_message(call.message.chat.id, f"⏳ Sizga {time_limit//60} minut {time_limit%60} sekund vaqt berildi. Test boshlandi!")
    bot.edit_message_text("Test boshlandi!", call.message.chat.id, call.message.message_id)
    send_question_simple(call.message.chat.id, user_data[user_id])

def get_test_time_limit(num_questions):
    if num_questions == 5:
        return 60
    elif num_questions == 10:
        return 120
    elif num_questions == 15:
        return 240
    elif num_questions == 30:
        return 300
    else:
        return None  # Cheklov yo'q yoki default

def send_question_simple(chat_id, data):
    q_num = data['current_question']
    if q_num >= len(data['questions']):
        show_results_simple(chat_id, data)
        return
    question_data = data['questions'][q_num]
    markup = types.InlineKeyboardMarkup(row_width=1)
    text = f"*Savol {q_num + 1}/{len(data['questions'])}*\n\n{question_data['question']}\n\n"
    for i, option in enumerate(question_data["options"]):
        callback_data = f"answer_simple_{i}"
        markup.add(types.InlineKeyboardButton(option, callback_data=callback_data))
    text += "Javobni tanlang:"
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('answer_simple_'))
def handle_answer_simple(call):
    user_id = call.from_user.id
    data = user_data.get(user_id)
    if not data:
        bot.answer_callback_query(call.id, "Xatolik!")
        return
    i = int(call.data.replace('answer_simple_', ''))
    question_data = data['questions'][data['current_question']]
    letter_to_index = {"A": 0, "B": 1, "C": 2, "D": 3}
    correct = question_data["correct"]
    correct_index = letter_to_index.get(str(correct).strip().upper(), 0)
    is_correct = (i == correct_index)
    user_answer = question_data["options"][i]
    correct_answer = question_data["options"][correct_index]
    if is_correct:
        data['score'] += 1
    else:
        data['wrong_answers'].append({
            "savol": question_data["question"],
            "sizning_javobingiz": user_answer,
            "togri_javob": correct_answer
        })
    data['current_question'] += 1
    bot.answer_callback_query(call.id)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    send_question_simple(call.message.chat.id, data)

def show_results_simple(chat_id, data):
    score = data['score']
    total = len(data['questions'])
    percent = (score / total) * 100
    if percent >= 80:
        grade = "A'lo 🏆"
    elif percent >= 60:
        grade = "Yaxshi 👍"
    elif percent >= 40:
        grade = "Qoniqarli 👌"
    else:
        grade = "Qoniqarsiz 📚"
    text = f"""✅ *Test yakunlandi!*
\n📊 *Natija:* {score}/{total}\n📈 *Foiz:* {percent:.1f}%\n🎓 *Baho:* {grade}\n"""
    bot.send_message(chat_id, text, parse_mode='Markdown')
    if data['wrong_answers']:
        wrongs = "\n\n❌ *Xato javoblaringiz:*\n"
        for i, item in enumerate(data['wrong_answers'], 1):
            explanation = ""
            for q in data['questions']:
                if q["question"] == item["savol"]:
                    explanation = q.get("explanation", "")
                    break
            wrongs += f"\n{i}. {item['savol']}\nSiz: {item['sizning_javobingiz']}\nTo'g'ri: {item['togri_javob']}\n"
            if explanation:
                wrongs += f"Tushuntirish: {explanation}\n"
        bot.send_message(chat_id, wrongs, parse_mode='Markdown')
    # Test tugaganda sessionni tozalash
    for k in list(user_data.keys()):
        if user_data[k] is data:
            del user_data[k]
            break

# Eski handle_answer funksiyasi va handleri olib tashlandi, faqat answer_simple_ uchun handler qoldi.

# Test paytida /stop tugmasi bosilsa, session tozalanadi
def handle_stop_during_quiz(call):
    if call.data.startswith('stop_'):
        user_id = int(call.data.replace('stop_', ''))
        if user_id == call.from_user.id:
            if user_id in user_data:
                del user_data[user_id]
            bot.edit_message_text("❌ Test to'xtatildi. /start orqali qayta boshlang.", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "Faqat o'zingiz to'xtata olasiz!")

bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))(handle_stop_during_quiz)

# ================== Natija =============================
def show_results(chat_id, user_id):
    session = get_user_session(user_id)
    session.is_active = False
    if session.timer:
        session.timer.cancel()
        session.timer = None
    score = session.score
    total = len(session.questions)
    if total == 0:
        bot.send_message(chat_id, "❌ Test savollari topilmadi yoki yuklanmadi.")
        return
    percent = (score / total) * 100
    if percent >= 80:
        grade = "A'lo 🏆"
    elif percent >= 60:
        grade = "Yaxshi 👍"
    elif percent >= 40:
        grade = "Qoniqarli 👌"
    else:
        grade = "Qoniqarsiz 📚"
    test_name = getattr(session, 'test_name', 'default')
    # Test qancha vaqtda bajarilganini hisoblash
    vaqt = "-"
    if hasattr(session, 'test_start_time'):
        seconds = int(time.time() - session.test_start_time)
        if seconds < 60:
            vaqt = f"{seconds} sekund"
        else:
            vaqt = f"{seconds//60} minut {seconds%60} sekund"
    save_result(user_id, session.full_name, session.phone_number, score, test_name)
    text = f"""✅ *Test yakunlandi!*

👤 *Ism:* {session.full_name}
📞 *Tel:* {session.phone_number}
📊 *Natija:* {score}/{total}
📈 *Foiz:* {percent:.1f}%
⏱ *Siz bu testni {vaqt} ichida bajardingiz.*
🎓 *Baho:* {grade}

➡️ Yana test ishlamoqchi bo‘lsangiz /quiz ni bosing.

_Bu bot @Yunusbek0 tomonidan yaratilgan_"""
    bot.send_message(chat_id, text, parse_mode='Markdown')
    if session.wrong_answers:
        wrongs = "\n\n❌ *Xato javoblaringiz:*\n"
        for i, item in enumerate(session.wrong_answers, 1):
            wrongs += f"\n{i}. {item['savol']}\nSiz: {item['sizning_javobingiz']}\nTo'g'ri: {item['togri_javob']}\n"
        bot.send_message(chat_id, wrongs, parse_mode='Markdown')

# ================== Admin buyrug'i ======================
@bot.message_handler(commands=['results'])
def admin_results(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    export_results_to_csv()
    with open("results.csv", "rb") as f:
        bot.send_document(message.chat.id, f)

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id in ADMIN_IDS:
        bot.send_message(
            message.chat.id,
            "Admin panelga xush kelibsiz!\nBuyruqlar:\n /results - Natijalarni ko‘rish\n/new - Yangi test yuklash\n/deletetest - Test faylini o‘chirish\n/results_del - Natijalarni tozalash"
        )
    else:
        bot.send_message(message.chat.id, "Siz admin emassiz!")

@bot.message_handler(commands=['deletetest'])
def delete_test_command(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "Faqat admin testlarni o'chira oladi!")
        return
    test_files = [f for f in glob.glob("*.xlsx") if not f.startswith("~$")]
    if not test_files:
        bot.send_message(message.chat.id, "Hech qanday test fayli topilmadi.")
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    for fname in test_files:
        markup.add(types.InlineKeyboardButton(fname, callback_data=f"delete_test_{fname}"))
    bot.send_message(message.chat.id, "Qaysi test faylini o'chirmoqchisiz?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_test_'))
def handle_delete_test(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Faqat admin o'chira oladi!")
        return
    fname = call.data.replace('delete_test_', '')
    try:
        os.remove(fname)
        bot.answer_callback_query(call.id, f"{fname} o'chirildi!")
        bot.send_message(call.message.chat.id, f"✅ {fname} test fayli o'chirildi!")
    except Exception as e:
        bot.answer_callback_query(call.id, "Xatolik!")
        bot.send_message(call.message.chat.id, f"❌ Xatolik: {e}")

@bot.message_handler(commands=['results_del'])
def results_del_command(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.send_message(message.chat.id, "Faqat admin natijalarni tozalay oladi!")
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Ha, o'chirilsin", callback_data="confirm_results_del"),
        types.InlineKeyboardButton("Yo'q, bekor qilish", callback_data="cancel_results_del")
    )
    bot.send_message(message.chat.id, "Rostan ham barcha natijalarni va results.csv faylini o'chirmoqchimisiz?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["confirm_results_del", "cancel_results_del"])
def handle_results_del_confirm(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Faqat admin tozalay oladi!")
        return
    if call.data == "confirm_results_del":
        # results.csv faylini o'chirish
        try:
            if os.path.exists("results.csv"):
                os.remove("results.csv")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"results.csv o'chirishda xatolik: {e}")
            return
        # SQLite bazadagi results jadvalini tozalash
        try:
            conn = sqlite3.connect("quiz.db")
            c = conn.cursor()
            c.execute("DELETE FROM results")
            conn.commit()
            conn.close()
        except Exception as e:
            bot.send_message(call.message.chat.id, f"Bazani tozalashda xatolik: {e}")
            return
        bot.edit_message_text("✅ Natijalar va results.csv tozalandi!", call.message.chat.id, call.message.message_id)
    else:
        bot.edit_message_text("❌ Tozalash bekor qilindi.", call.message.chat.id, call.message.message_id)

# ================== Qo'shimcha buyruqlar ===================
@bot.message_handler(commands=['help'])
def help_command(message):
    text = """📌 *Botdan foydalanish bo'yicha yordam:*

/start - Ro'yxatdan o'tish va telefon tasdiqlash
/quiz - Testni boshlash (faqat tasdiqlangan foydalanuvchilar)
/help - Yordam
/restart - Boshidan boshlash
/admin - Faqat admin uchun menyu

📱 *Telefon tasdiqlash jarayoni:*
1. /start - ism va familiya kiriting
2. Telefon raqamni yuboring
3. Kelgan kodni kiriting
4. /quiz - testni boshlang
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['restart'])
def restart_command(message):
    user_id = message.from_user.id
    if user_id in user_data:
        del user_data[user_id]
    bot.send_message(message.chat.id, "Bot qayta ishga tushdi. /start ni bosib boshlang.")

@bot.message_handler(commands=['stop'])
def stop_command(message):
    user_id = message.from_user.id
    if user_id in user_data:
        session = user_data[user_id]
        if session.timer:
            session.timer.cancel()
            session.timer = None
        del user_data[user_id]
    bot.send_message(message.chat.id, "❌ Jarayon to'xtatildi. /start orqali qayta boshlang.")

# ================== Ishga tushirish =====================
def main():
    init_db()
    print("🤖 Bot ishga tushdi...")
    bot.infinity_polling()

if __name__ == '__main__':

    main()
