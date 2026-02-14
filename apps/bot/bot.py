import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from django.conf import settings
from django.db.models import Count, Q
from apps.content.models import Test, Category, Question
from apps.bot.models import TelegramUser, UserCategoryProgress, UserAnswer
from telebot.apihelper import ApiTelegramException

bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN, threaded=False)


@bot.message_handler(commands=['start'])
def handle_start(message: Message) -> None:
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
def show_topics(call: CallbackQuery) -> None:
    subject_id = int(call.data.split(':')[1])
    user_id = call.from_user.id

    user = TelegramUser.objects.get(telegram_id=user_id)
    subject = Test.objects.get(id=subject_id)

    # Optimization: Annotate with question count
    topics = Category.objects.filter(test=subject).annotate(total_questions=Count('question'))

    # Optimization: Fetch all progress for these topics in one query
    progress_qs = UserCategoryProgress.objects.filter(user=user, category__in=topics)
    progress_map = {p.category_id: p for p in progress_qs}

    markup = InlineKeyboardMarkup()

    for topic in topics:
        prog = progress_map.get(topic.id)

        if prog and prog.total_answered > 0:
            total_q = topic.total_questions
            btn_text = f"{topic.name} ({prog.correct_count}/{total_q})"

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


def get_next_question(user: TelegramUser, category_id: int) -> Question | None:
    """
    Fetches the next question.
    PRIORITY 1: Questions marked for retry (is_active=False)
    PRIORITY 2: New questions (not in UserAnswer with is_active=True)
    """

    retry_q = Question.objects.filter(
        category_id=category_id,
        useranswer__user=user,
        useranswer__is_active=False
    ).select_related('category').defer('explanation').order_by('page_number', 'question_number', 'id').first()

    if retry_q:
        return retry_q

    return Question.objects.filter(
        category_id=category_id
    ).exclude(
        useranswer__user=user,
        useranswer__is_active=True
    ).select_related('category').defer('explanation').order_by('page_number', 'question_number', 'id').first()


@bot.callback_query_handler(func=lambda call: call.data.startswith('topic:'))
def start_quiz(call: CallbackQuery) -> None:
    bot.answer_callback_query(call.id)
    topic_id = int(call.data.split(':')[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)
    category = Category.objects.get(id=topic_id)

    total_q = Question.objects.filter(category=category).count()

    # Optimization: Use aggregate to fetch all stats in one query
    stats = UserAnswer.objects.filter(user=user, question__category=category).aggregate(
        correct_count=Count('id', filter=Q(is_correct=True, is_active=True)),
        active_mistakes=Count('id', filter=Q(is_correct=False, is_active=True)),
        pending_retries=Count('id', filter=Q(is_correct=False, is_active=False))
    )

    correct_count = stats['correct_count']
    active_mistakes = stats['active_mistakes']
    pending_retries = stats['pending_retries']

    if pending_retries > 0:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(f"▶️ Resume Retry ({pending_retries})", callback_data=f"resume_retry:{topic_id}"),
            InlineKeyboardButton("🔄 Full Reset", callback_data=f"reset:{topic_id}")
        )
        markup.add(InlineKeyboardButton("🔙 Menu", callback_data="start_menu"))

        text = (
            f"⚠️ **Paused Progress**\n\n"
            f"You have **{pending_retries}** incorrect questions waiting for retry.\n"
            f"✅ Correct: {correct_count}\n"
            f"❌ Active Mistakes: {active_mistakes}\n"
            f"What would you like to do?"
        )
        bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
        return

    if (correct_count + active_mistakes) >= total_q and total_q > 0:
        send_result_screen(user_id, category, correct_count, active_mistakes, total_q)
        return

    question = get_next_question(user, topic_id)

    if not question:
        send_result_screen(user_id, category, correct_count, active_mistakes, total_q)
        return

    send_question_card(call.message.chat.id, question)


@bot.callback_query_handler(func=lambda call: call.data.startswith('resume_retry:'))
def handle_resume_retry(call: CallbackQuery) -> None:
    topic_id = int(call.data.split(':')[1])
    user_id = call.message.chat.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    question = get_next_question(user, topic_id)
    if question:
        send_question_card(user_id, question)
    else:
        bot.send_message(user_id, "🎉 No questions left!")


def send_result_screen(user_id, category, correct, wrong, total):
    text = (
        f"🏁 **Category Finished!**\n\n"
        f"✅ Correct: {correct}\n"
        f"❌ Incorrect: {wrong}\n"
        f"📚 Total: {total}"
    )
    markup = InlineKeyboardMarkup(row_width=1)

    if wrong > 0:
        markup.add(InlineKeyboardButton(
            f"🔄 Retry {wrong} Incorrect Questions",
            callback_data=f"retry_fail:{category.id}"
        ))

    markup.add(
        InlineKeyboardButton("🔄 Full Reset", callback_data=f"reset:{category.id}"),
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu")
    )

    bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")


def format_question_text(question: Question, category_progress: dict[str, int]) -> str:
    current = category_progress['current']
    total = category_progress['total']

    progress = 0
    bar_length = 15
    if total > 0:
        progress = current / total

    filled_length = int(bar_length * progress)

    if current > 0 and filled_length == 0:
        filled_length = 1

    bar = "▓" * filled_length + "░" * (bar_length - filled_length)

    progress_info = f"`{bar}` {int(progress * 100)}% • {current}/{total}"

    header = f"📂 *{question.category.name}*"
    if question.subcategory:
        header += f" | {question.subcategory}"

    q_num = f" {question.question_number}" if question.question_number else ""
    options_text = "\n".join(question.options)

    text = (
        f"{progress_info}\n"
        f"{header}\n"
        f"❓ *Question{q_num}*\n\n"
        f"{question.text}\n\n"
        f"{options_text}"
    )
    return text


def send_question_card(chat_id: int, question: Question) -> None:
    user = TelegramUser.objects.get(telegram_id=chat_id)
    category = question.category

    total_questions = Question.objects.filter(category=category).count()

    passed_questions = UserAnswer.objects.filter(
        user=user,
        question__category=category,
        is_active=True
    ).count()

    category_progress = {'current': passed_questions, 'total': total_questions}
    text = format_question_text(question, category_progress)

    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(text=letter, callback_data=f"ans:{question.id}:{letter}")
        for letter in ("A", "B", "C", "D", "E")
    ]

    markup.add(
        *buttons,
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu"),
        InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{question.category.id}")
    )

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: call.data.startswith('ans:'))
def handle_answer(call: CallbackQuery) -> None:
    bot.answer_callback_query(call.id)
    _, q_id, selected = call.data.split(':')
    q_id = int(q_id)
    user_id = call.from_user.id

    try:
        user = TelegramUser.objects.get(telegram_id=user_id)
        question = Question.objects.select_related('category').get(id=q_id)
    except Exception:
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
    prog.save()

    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
    pdf_upload = question.category.pdfupload_set.first()
    pdf_link = ""
    if pdf_upload:
        page_for_link = question.page_number
        pdf_link = f"[📖 Open PDF (Page {page_for_link})]({site_url}{pdf_upload.file.url}#page={page_for_link})"

    explanation = question.explanation if question.explanation else "Not found"
    if is_correct:
        response = f"✅ **Correct!**\n💡 **Explanation:**\n_{explanation}_\n\n{pdf_link}"
    else:
        response = (
            f"❌ **Wrong!**\n"
            f"You chose: **{selected}**\n"
            f"Correct: **{question.correct_option}**\n\n"
            f"💡 **Explanation:**\n_{explanation}_\n\n"
            f"{pdf_link}"
        )

    total_questions = Question.objects.filter(category=question.category).count()
    passed_questions_count = UserAnswer.objects.filter(
        user=user, question__category=question.category, is_active=True
    ).count()

    category_progress = {'current': passed_questions_count, 'total': total_questions}
    clean_question_text = format_question_text(question, category_progress)

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("➡️ Next Question", callback_data=f"next:{question.category.id}"),
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu"),
        InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{question.category.id}")
    )

    try:
        bot.edit_message_text(
            f"{clean_question_text}\n\n{response}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=markup
        )
    except ApiTelegramException as exc:
        if "message is not modified" not in str(exc):
            raise


@bot.callback_query_handler(func=lambda call: call.data.startswith('next:'))
def handle_next_question(call: CallbackQuery) -> None:
    bot.answer_callback_query(call.id)

    topic_id = int(call.data.split(':')[1])
    user_id = call.message.chat.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    question = get_next_question(user, topic_id)
    if question:
        send_question_card(user_id, question)
    else:
        category = Category.objects.get(id=topic_id)
        total_q = Question.objects.filter(category=category).count()
        correct_count = UserAnswer.objects.filter(user=user, question__category=category, is_correct=True, is_active=True).count()
        mistakes_count = UserAnswer.objects.filter(user=user, question__category=category, is_correct=False, is_active=True).count()
        send_result_screen(user_id, category, correct_count, mistakes_count, total_q)


@bot.callback_query_handler(func=lambda call: call.data.startswith('reset:'))
def reset_progress_handler(call: CallbackQuery) -> None:
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


@bot.callback_query_handler(func=lambda call: call.data.startswith('retry_fail:'))
def handle_retry_fail(call: CallbackQuery) -> None:
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
def back_to_start(call: CallbackQuery) -> None:
    class FakeMessage:
        def __init__(self, user_id: int, first_name: str, username: str) -> None:
            self.from_user = type('User', (), {'id': user_id, 'first_name': first_name, 'username': username})()
            self.chat = type('Chat', (), {'id': user_id})()

    msg = FakeMessage(call.from_user.id, call.from_user.first_name, call.from_user.username)
    handle_start(msg)
