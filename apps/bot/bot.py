import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from django.conf import settings

# Import models from BOTH apps
from apps.content.models import Test, Category, Question
from apps.bot.models import TelegramUser, UserCategoryProgress, UserAnswer
from telebot.apihelper import ApiTelegramException


# Initialize Bot
bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN, threaded=False)


# --- 1. START: Register User & Show Subjects (Tests) ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    # 1. Register User in DB
    TelegramUser.objects.get_or_create(
        telegram_id=user_id,
        defaults={'username': username, 'first_name': first_name}
    )

    # 2. Fetch Subjects (e.g. DAHİLİYE, PEDİATRİ)
    subjects = Test.objects.all()

    # 3. Build Keyboard
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [
        # Callback: "subj:ID"
        InlineKeyboardButton(sub.name, callback_data=f"subj:{sub.id}")
        for sub in subjects
    ]
    markup.add(*buttons)

    welcome_msg = f"👋 **Hello {first_name}!**\nSelect a subject to start practicing:"
    bot.send_message(user_id, welcome_msg, reply_markup=markup, parse_mode="Markdown")


# --- 2. MENU: Show Topics (Categories) inside a Subject ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('subj:'))
def show_topics(call):
    subject_id = int(call.data.split(':')[1])
    user_id = call.from_user.id

    # Get the User object
    user = TelegramUser.objects.get(telegram_id=user_id)
    subject = Test.objects.get(id=subject_id)

    # Get all topics for this subject
    topics = Category.objects.filter(test=subject)

    markup = InlineKeyboardMarkup()

    for topic in topics:
        # Check progress
        prog = UserCategoryProgress.objects.filter(user=user, category=topic).first()

        # Format: "Hematoloji (5/50)" or just "Hematoloji"
        if prog and prog.total_answered > 0:
            total_q = Question.objects.filter(category=topic).count()
            btn_text = f"{topic.name} ({prog.correct_count}/{prog.total_answered})"

            # If completed, add a checkmark
            if prog.total_answered >= total_q:
                btn_text = "✅ " + btn_text
        else:
            btn_text = topic.name

        markup.add(InlineKeyboardButton(btn_text, callback_data=f"topic:{topic.id}"))

    markup.add(InlineKeyboardButton("🔙 Back", callback_data="start_menu"))

    try:
        bot.edit_message_text(
            f"📂 **Subject:** {subject.name}\nChoose a topic:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except ApiTelegramException as exc:
        if "message is not modified" not in str(exc):
            raise


# --- 3. QUIZ ENGINE: Find Next Question ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('topic:'))
def start_quiz(call):
    topic_id = int(call.data.split(':')[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    # Find questions the user has ALREADY answered
    answered_ids = UserAnswer.objects.filter(
        user=user,
        question__category_id=topic_id
    ).values_list('question_id', flat=True)

    # Find the first question NOT in that list
    question = Question.objects.filter(category_id=topic_id).exclude(id__in=answered_ids).order_by('question_number', 'id').first()

    if not question:
        # Category Finished! Show Reset Option
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{topic_id}"),
            InlineKeyboardButton("🔙 Menu", callback_data=f"subj:{Question.objects.get(category_id=topic_id).category.test.id}")
        )

        bot.send_message(call.message.chat.id, "🎉 **Category Completed!**\nYou have answered all questions.", reply_markup=markup, parse_mode="Markdown")
        return

    send_question_card(call.message.chat.id, question)


def send_question_card(chat_id, question):
    q_num = f" {question.question_number}" if question.question_number else ""
    options_text = "\n".join(question.options)

    text = (
        f"-----------------------------\n"
        f"❓ **Question{q_num}**\n\n"
        f"{question.text}\n\n"
        f"{options_text}"
    )

    markup = InlineKeyboardMarkup(row_width=2)

    # Strictly hardcoded A, B, C, D, E — no loops over the options text!
    buttons = [
        InlineKeyboardButton(
            text=letter,
            callback_data=f"ans:{question.id}:{letter}"
        )
        for letter in ("A", "B", "C", "D", "E")
    ]

    markup.add(
        *buttons,
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu"),
        InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{question.category.id}")
    )

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")


# --- 4. ANSWER HANDLER: Check & Save ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('ans:'))
def handle_answer(call):
    _, q_id, selected = call.data.split(':')
    q_id = int(q_id)
    user_id = call.from_user.id

    try:
        user = TelegramUser.objects.get(telegram_id=user_id)
        question = Question.objects.get(id=q_id)
    except Exception:
        bot.answer_callback_query(call.id, "Error: Data missing.")
        return None

    is_correct = (selected == question.correct_option)

    # 1. Save Attempt
    UserAnswer.objects.update_or_create(
        user=user, question=question,
        defaults={'selected_option': selected, 'is_correct': is_correct}
    )

    # 2. Update Progress Stats
    prog, _ = UserCategoryProgress.objects.get_or_create(user=user, category=question.category)
    prog.total_answered += 1
    if is_correct:
        prog.correct_count += 1
    prog.save()

    if is_correct:
        response = f"✅ **Correct!**\n\n_{question.explanation}_"
    else:
        site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
        pdf_upload = question.category.pdfupload_set.first()

        pdf_link = ""
        if pdf_upload:
            page_for_link = question.page_number + 1
            pdf_link = f"[📖 Open PDF (Page {page_for_link})]({site_url}{pdf_upload.file.url}#page={page_for_link})"

        response = (
            f"❌ **Wrong!**\n"
            f"You chose: **{selected}**\n"
            f"Correct: **{question.correct_option}**\n\n"
            f"💡 **Explanation:**\n_{question.explanation}_\n\n"
            f"{pdf_link}"
        )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("➡️ Next Question", callback_data=f"topic:{question.category.id}"),
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu"),
        InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{question.category.id}")
    )

    try:
        # Edit the message to show result (removes buttons)
        bot.edit_message_text(
            f"{call.message.text}\n\n{response}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=markup
        )
    except ApiTelegramException as exc:
        if "message is not modified" not in str(exc):
            raise


# --- 5. RESET HANDLER ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('reset:'))
def reset_progress_handler(call):
    topic_id = int(call.data.split(':')[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    try:
        prog = UserCategoryProgress.objects.get(user=user, category_id=topic_id)
        prog.reset_progress()  # Call the model method we wrote!
        bot.answer_callback_query(call.id, "History cleared!")

        # Restart quiz immediately
        start_quiz(call)
    except Exception:
        bot.answer_callback_query(call.id, "Nothing to reset.")


# --- 6. NAVIGATION HELPERS ---
@bot.callback_query_handler(func=lambda call: call.data == "start_menu")
def back_to_start(call):
    # Just call the start logic again
    # We fake a message object
    class FakeMessage:
        def __init__(self, user_id, first_name, username):
            self.from_user = type('User', (), {'id': user_id, 'first_name': first_name, 'username': username})()
            self.chat = type('Chat', (), {'id': user_id})()

    msg = FakeMessage(call.from_user.id, call.from_user.first_name, call.from_user.username)
    handle_start(msg)
