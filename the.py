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

# ================= Bot token ===================
BOT_TOKEN = "7637437071:AAFiNVYnPqykWpWtrf9XsBKc9gHNEvejt58"
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
@bot.message_handler(commands=['quiz'])
def start_quiz(message):
    user_id = message.from_user.id
    session = get_user_session(user_id)

    # Verifikatsiya tekshirish
    if session.verification_step != "completed":
        bot.send_message(message.chat.id, 
            "❌ Avval telefon raqamingizni tasdiqlashingiz kerak!\n"
            "/start buyrug'i bilan qaytadan boshlang.")
        return

    # Mavjud .xlsx fayllarni topish
    test_files = [f for f in glob.glob("*.xlsx") if not f.startswith("~$")]
    if not test_files:
        bot.send_message(message.chat.id, "Hech qanday test fayli topilmadi.")
        return

    # Fayl nomidan test nomini chiqarish
    test_names = [f.replace(".xlsx", "") for f in test_files]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for name in test_names:
        markup.add(types.InlineKeyboardButton(name.title(), callback_data=f"select_test_{name}"))
    bot.send_message(message.chat.id, "Qaysi testni tanlaysiz?", reply_markup=markup)

# handle_select_test va show_results funksiyalarida test nomini save_result ga uzatish
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_test_'))
def handle_select_test(call):
    user_id = call.from_user.id
    session = get_user_session(user_id)
    test_name = call.data.replace('select_test_', '')
    file_name = f"{test_name}.xlsx"
    try:
        session.questions = load_questions_from_excel(file_name)
        if not session.questions:
            bot.send_message(call.message.chat.id, f"❌ Bu test faylida savollar topilmadi: {file_name}")
            return
        session.score = 0
        session.current_question = 0
        session.is_active = True
        session.wrong_answers = []
        session.test_name = test_name  # test nomini sessionga saqlaymiz
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_question(call.message.chat.id, user_id)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Xatolik: {e}\nFayl: {file_name}")


def send_question(chat_id, user_id):
    session = get_user_session(user_id)
    q_num = session.current_question

    if q_num >= len(session.questions):
        show_results(chat_id, user_id)
        return

    question_data = session.questions[q_num]
    markup = types.InlineKeyboardMarkup(row_width=1)

    for i, option in enumerate(question_data["options"]):
        callback_data = f"answer_{user_id}_{i}"
        markup.add(types.InlineKeyboardButton(option, callback_data=callback_data))
    # /stop tugmasini har bir savolda qo'shamiz
    markup.add(types.InlineKeyboardButton("/stop", callback_data=f"stop_{user_id}"))

    text = (
        f"*Savol {q_num + 1}/{len(session.questions)}*\n\n"
        f"{question_data['question']}\n\n"
        "Javobni tanlang:"
    )
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('answer_'))
def handle_answer(call):
    try:
        _, user_id, selected = call.data.split('_')
        user_id = int(user_id)
        selected = int(selected)

        if call.from_user.id != user_id:
            bot.answer_callback_query(call.id, "Bu sizning savolingiz emas!")
            return

        session = get_user_session(user_id)

        if not session.is_active:
            bot.answer_callback_query(call.id, "Test allaqachon tugagan.")
            return

        question_data = session.questions[session.current_question]
        if selected == question_data["correct"]:
            session.score += 1
        else:
            session.wrong_answers.append({
                "savol": question_data["question"],
                "sizning_javobingiz": question_data["options"][selected],
                "togri_javob": question_data["options"][question_data["correct"]]
            })

        session.current_question += 1
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_question(call.message.chat.id, user_id)

    except Exception as e:
        print(f"Xatolik: {e}")
        bot.answer_callback_query(call.id, "Xatolik yuz berdi!")

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

    # test nomini sessiondan olamiz, yo'q bo'lsa 'default'
    test_name = getattr(session, 'test_name', 'default')
    save_result(user_id, session.full_name, session.phone_number, score, test_name)

    text = f"""✅ *Test yakunlandi!*

👤 *Ism:* {session.full_name}
📞 *Tel:* {session.phone_number}
📊 *Natija:* {score}/{total}
📈 *Foiz:* {percent:.1f}%
🎓 *Baho:* {grade}\n\n_Bu bot @Yunusbek0 tomonidan yaratilgan_"""
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
    # Savollarni yangilash
    global QUESTIONS
    QUESTIONS = load_questions_from_excel(file_name)
    bot.send_message(message.chat.id, f"✅ Yangi test fayli yuklandi va savollar yangilandi! ({file_name})")

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
        del user_data[user_id]
    bot.send_message(message.chat.id, "❌ Jarayon to'xtatildi. /start orqali qayta boshlang.")

# ================== Ishga tushirish =====================
def main():
    init_db()
    print("🤖 Bot ishga tushdi...")
    bot.infinity_polling()

if __name__ == '__main__':
    main()
