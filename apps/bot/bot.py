import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from django.conf import settings
from apps.content.models import Test, Category, Question
from apps.bot.models import TelegramUser, UserCategoryProgress, UserAnswer
from telebot.apihelper import ApiTelegramException

bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN, threaded=False)


@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    TelegramUser.objects.get_or_create(
        telegram_id=user_id,
        defaults={'username': username, 'first_name': first_name}
    )

    subjects = Test.objects.all()

    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(sub.name, callback_data=f"subj:{sub.id}")
        for sub in subjects
    ]
    markup.add(*buttons)

    welcome_msg = f"👋 **Hello {first_name}!**\nSelect a subject to start practicing:"
    bot.send_message(user_id, welcome_msg, reply_markup=markup, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data.startswith('subj:'))
def show_topics(call):
    subject_id = int(call.data.split(':')[1])
    user_id = call.from_user.id

    user = TelegramUser.objects.get(telegram_id=user_id)
    subject = Test.objects.get(id=subject_id)

    topics = Category.objects.filter(test=subject)

    markup = InlineKeyboardMarkup()

    for topic in topics:
        prog = UserCategoryProgress.objects.filter(user=user, category=topic).first()

        if prog and prog.total_answered > 0:
            total_q = Question.objects.filter(category=topic).count()
            btn_text = f"{topic.name} ({prog.correct_count}/{prog.total_answered})"

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


def get_next_question(user, category_id):
    retry_ids = UserAnswer.objects.filter(
        user=user,
        question__category_id=category_id,
        is_active=False 
    ).values_list('question_id', flat=True)

    if retry_ids:
        return Question.objects.filter(id__in=retry_ids).order_by('page_number', 'question_number', 'id').first()

    active_answered_ids = UserAnswer.objects.filter(
        user=user,
        question__category_id=category_id,
        is_active=True
    ).values_list('question_id', flat=True)

    return Question.objects.filter(
        category_id=category_id
    ).exclude(
        id__in=active_answered_ids
    ).order_by('page_number', 'question_number', 'id').first()


@bot.callback_query_handler(func=lambda call: call.data.startswith('topic:'))
def start_quiz(call):
    topic_id = int(call.data.split(':')[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    answered_ids = UserAnswer.objects.filter(
        user=user,
        question__category_id=topic_id
    ).values_list('question_id', flat=True)

    question = Question.objects.filter(category_id=topic_id).exclude(id__in=answered_ids).order_by('page_number', 'question_number', 'id').first()

    if not question:
        total_q = Question.objects.filter(category_id=topic_id).count()

        correct_count = UserAnswer.objects.filter(
            user=user,
            question__category_id=topic_id,
            is_correct=True,
            is_active=True
        ).count()

        mistakes_count = UserAnswer.objects.filter(
            user=user,
            question__category_id=topic_id,
            is_correct=False,
            is_active=True
        ).count()

        result_text = (
            f"🏁 **Category Finished!**\n\n"
            f"✅ Correct: {correct_count}\n"
            f"❌ Incorrect: {mistakes_count}\n"
            f"📚 Total: {total_q}"
        )

        markup = InlineKeyboardMarkup(row_width=1)

        if mistakes_count > 0:
            markup.add(InlineKeyboardButton(
                f"🔄 Retry {mistakes_count} Incorrect Questions",
                callback_data=f"retry_fail:{topic_id}"
            ))

        markup.add(
            InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{topic_id}"),
            InlineKeyboardButton("🔙 Menu", callback_data=f"subj:{Question.objects.filter(category_id=topic_id).first().category.test.id}")
        )

        bot.send_message(call.message.chat.id, result_text, reply_markup=markup, parse_mode="Markdown")
        return

    send_question_card(call.message.chat.id, question)


def send_question_card(chat_id, question):
    user = TelegramUser.objects.get(telegram_id=chat_id)
    category = question.category

    total_questions = Question.objects.filter(category=category).count()
    passed_questions = UserAnswer.objects.filter(
        user=user,
        question__category=category,
        is_active=True
    ).count()

    progress = 0
    bar_length = 25
    if total_questions > 0:
        progress = passed_questions / total_questions

    filled_length = int(bar_length * progress)

    if passed_questions > 0 and filled_length == 0:
        filled_length = 1

    bar = "●" * filled_length + "○" * (bar_length - filled_length)

    progress_info = f"`{bar}` {passed_questions}/{total_questions}"

    sub_text = f"📂 *{question.subcategory}*\n" if question.subcategory else ""
    q_num = f" {question.question_number}" if question.question_number else ""
    options_text = "\n".join(question.options)

    text = (
        f"{progress_info}\n"
        f"{sub_text}"
        f"❓ **Question{q_num}**\n\n"
        f"{question.text}\n\n"
        f"{options_text}"
    )

    markup = InlineKeyboardMarkup(row_width=2)

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

    UserAnswer.objects.update_or_create(
        user=user, question=question,
        defaults={
            'selected_option': selected,
            'is_correct': is_correct,
            'is_active': True
        }
    )

    prog, _ = UserCategoryProgress.objects.get_or_create(user=user, category=question.category)
    prog.total_answered += 1
    if is_correct:
        prog.correct_count += 1

    prog.save(update_fields=['total_answered', 'correct_count'])

    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
    pdf_upload = question.category.pdfupload_set.first()

    pdf_link = ""
    if pdf_upload:
        page_for_link = question.page_number
        pdf_link = f"[📖 Open PDF (Page {page_for_link})]({site_url}{pdf_upload.file.url}#page={page_for_link})"

    if is_correct:
        response = (
            f"✅ **Correct!**\n"
            f"💡 **Explanation:**\n_{question.explanation}_\n\n"
            f"{pdf_link}"
        )
    else:
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


@bot.callback_query_handler(func=lambda call: call.data.startswith('reset:'))
def reset_progress_handler(call):
    topic_id = int(call.data.split(':')[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    UserAnswer.objects.filter(user=user, question__category_id=topic_id).delete()
    UserCategoryProgress.objects.filter(user=user, category_id=topic_id).update(
        correct_count=0, total_answered=0
    )

    bot.answer_callback_query(call.id, "🔄 Full reset complete!")

    call.data = f"topic:{topic_id}"
    start_quiz(call)


@bot.callback_query_handler(func=lambda call: call.data.startswith('retry_fail'))
def handle_retry_fail(call):
    try:
        topic_id = int(call.data.split(":")[1])
        user_id = call.message.chat.id
        user = TelegramUser.objects.get(telegram_id=user_id)

        updated_rows = UserAnswer.objects.filter(
            user=user,
            question__category_id=topic_id,
            is_correct=False,
            is_active=True
        ).update(is_active=False)

        bot.answer_callback_query(call.id, f"Reloading {updated_rows} questions...")

        question = get_next_question(user, topic_id)
        if question:
            send_question_card(user_id, question)
        else:
            bot.send_message(user_id, "🎉 No questions left to retry!")

    except Exception as e:
        print(f"Error in retry handler: {e}")
        bot.send_message(call.message.chat.id, "❌ Error restarting quiz.")


@bot.callback_query_handler(func=lambda call: call.data == "start_menu")
def back_to_start(call):
    class FakeMessage:
        def __init__(self, user_id, first_name, username):
            self.from_user = type('User', (), {'id': user_id, 'first_name': first_name, 'username': username})()
            self.chat = type('Chat', (), {'id': user_id})()

    msg = FakeMessage(call.from_user.id, call.from_user.first_name, call.from_user.username)
    handle_start(msg)
