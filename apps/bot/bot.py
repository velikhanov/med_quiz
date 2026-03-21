import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from django.conf import settings
from django.db.models import Count, Q
from apps.content.models import Test, Category, Question
from apps.bot.models import TelegramUser, UserCategoryProgress, UserAnswer, PollMapping
from telebot.apihelper import ApiTelegramException

bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN, threaded=False)


@bot.message_handler(commands=["start"])
def handle_start(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    TelegramUser.objects.get_or_create(
        telegram_id=user_id,
        defaults={"username": username, "first_name": first_name}
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


@bot.callback_query_handler(func=lambda call: call.data.startswith("subj:"))
def show_topics(call: CallbackQuery) -> None:
    subject_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    user = TelegramUser.objects.get(telegram_id=user_id)
    subject = Test.objects.get(id=subject_id)

    # Optimization: Annotate with question count
    topics = Category.objects.filter(test=subject).annotate(total_questions=Count("question"))

    # Optimization: Fetch all progress for these topics in one query
    progress_qs = UserCategoryProgress.objects.filter(user=user, category__in=topics)
    progress_map = {p.category_id: p for p in progress_qs}

    markup = InlineKeyboardMarkup()

    for topic in topics:
        prog = progress_map.get(topic.id)

        if prog and prog.total_answered > 0:
            total_q = topic.total_questions
            btn_text = f"{topic.name} ({prog.total_answered}/{total_q})"

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
    ).select_related("category").defer("explanation").order_by("page_number", "question_number", "id").first()

    if retry_q:
        return retry_q

    return Question.objects.filter(
        category_id=category_id
    ).exclude(
        useranswer__user=user,
        useranswer__is_active=True
    ).select_related("category").defer("explanation").order_by("page_number", "question_number", "id").first()


@bot.callback_query_handler(func=lambda call: call.data.startswith("topic:"))
def start_quiz(call: CallbackQuery) -> None:
    bot.answer_callback_query(call.id)
    topic_id = int(call.data.split(":")[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)
    category = Category.objects.get(id=topic_id)

    total_q = Question.objects.filter(category=category).count()

    # Optimization: Use aggregate to fetch all stats in one query
    stats = UserAnswer.objects.filter(user=user, question__category=category).aggregate(
        correct_count=Count("id", filter=Q(is_correct=True, is_active=True)),
        active_mistakes=Count("id", filter=Q(is_correct=False, is_active=True)),
        pending_retries=Count("id", filter=Q(is_correct=False, is_active=False))
    )

    correct_count = stats["correct_count"]
    active_mistakes = stats["active_mistakes"]
    pending_retries = stats["pending_retries"]

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


@bot.callback_query_handler(func=lambda call: call.data.startswith("resume_retry:"))
def handle_resume_retry(call: CallbackQuery) -> None:
    topic_id = int(call.data.split(":")[1])
    user_id = call.message.chat.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    question = get_next_question(user, topic_id)
    if question:
        send_question_card(user_id, question)
    else:
        bot.send_message(user_id, "🎉 No questions left!")


def send_result_screen(user_id: int, category: Category, correct: int, wrong: int, total: int) -> None:
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


def send_question_card(chat_id: int, question: Question) -> None:
    user = TelegramUser.objects.get(telegram_id=chat_id)
    category = question.category

    total_questions = Question.objects.filter(category=category).count()
    passed_count = UserAnswer.objects.filter(user=user, question__category=category, is_active=True).count()

    # Progress header (plain text for poll)
    header = f"[{passed_count+1}/{total_questions}] {category.name}"
    if question.subcategory:
        header += f" | {question.subcategory}"

    # Poll question (Max 300 chars)
    poll_question = f"{header}\n\n{question.text}"[:300]

    # Options must be a list of strings (Max 100 chars each)
    clean_options = [opt[:100] for opt in question.options]

    # Find correct option index (A=0, B=1, etc.)
    correct_idx = ord(question.correct_option.upper()) - 65

    # Native explanation (Max 200 chars, no links)
    explanation = (question.explanation or "No explanation available.")[:200]

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu"),
        InlineKeyboardButton("🔄 Reset Progress", callback_data=f"reset:{question.category.id}")
    )

    try:
        poll_msg = bot.send_poll(
            chat_id=chat_id,
            question=poll_question,
            options=clean_options,
            type="quiz",
            correct_option_id=correct_idx,
            is_anonymous=False,
            explanation=explanation,
            reply_markup=markup
        )

        # Store the mapping so we know which question this poll belongs to when answered
        PollMapping.objects.create(
            poll_id=poll_msg.poll.id,
            question=question,
            user=user,
            chat_id=chat_id,
            message_id=poll_msg.message_id
        )
    except Exception as e:
        print(f"Error sending poll: {e}")
        bot.send_message(chat_id, f"❌ Failed to send poll. Error: {str(e)[:100]}")


@bot.poll_answer_handler()
def handle_poll_answer(poll_answer: telebot.types.PollAnswer) -> None:
    """Handles the user's interaction with the native poll."""
    try:
        mapping = PollMapping.objects.select_related("question", "user", "question__category").get(poll_id=poll_answer.poll_id)
    except PollMapping.DoesNotExist:
        return

    user = mapping.user
    question = mapping.question
    selected_idx = poll_answer.option_ids[0]
    selected_option = chr(65 + selected_idx)
    is_correct = (selected_idx == (ord(question.correct_option.upper()) - 65))

    # Record the answer
    UserAnswer.objects.update_or_create(
        user=user, question=question,
        defaults={
            "selected_option": selected_option,
            "is_correct": is_correct,
            "is_active": True
        }
    )

    # Update general stats
    prog, _ = UserCategoryProgress.objects.get_or_create(user=user, category=question.category)
    prog.total_answered = UserAnswer.objects.filter(user=user, question__category=question.category, is_active=True).count()
    prog.correct_count = UserAnswer.objects.filter(user=user, question__category=question.category, is_active=True, is_correct=True).count()
    prog.save()

    # Now update the poll's buttons to show "Next" and "PDF"
    markup = InlineKeyboardMarkup(row_width=1)

    markup.add(
        InlineKeyboardButton("➡️ Next Question", callback_data=f"next:{question.category.id}")
    )

    # Add Full Explanation button if it was truncated (Telegram limit is 200)
    if question.explanation and len(question.explanation) > 200:
        markup.add(InlineKeyboardButton("💡 Full Explanation", callback_data=f"expl:{question.id}"))

    # PDF Link Button
    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000")
    pdf_upload = question.category.pdfupload_set.first()
    if pdf_upload:
        page_link = f"{site_url}{pdf_upload.file.url}#page={question.page_number}"
        markup.add(InlineKeyboardButton(f"📖 Open PDF (Page {question.page_number})", url=page_link))

    markup.add(
        InlineKeyboardButton("🔙 Menu", callback_data="start_menu")
    )

    try:
        # Use .only() to fetch only what we need, and delete mapping to keep DB small
        bot.edit_message_reply_markup(mapping.chat_id, mapping.message_id, reply_markup=markup)
        mapping.delete()
    except Exception as e:
        print(f"Error updating reply markup: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("expl:"))
def handle_show_explanation(call: CallbackQuery) -> None:
    """Sends the full explanation as a separate message."""
    bot.answer_callback_query(call.id)
    q_id = int(call.data.split(":")[1])
    
    try:
        # Optimization: Only fetch the explanation field from the DB
        question = Question.objects.only("explanation").get(id=q_id)
        if question.explanation:
            text = f"💡 **Full Explanation:**\n\n{question.explanation}"
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
        else:
            bot.send_message(call.message.chat.id, "❌ No explanation found.")
    except Question.DoesNotExist:
        bot.send_message(call.message.chat.id, "❌ Question not found.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("next:"))
def handle_next_question(call: CallbackQuery) -> None:
    bot.answer_callback_query(call.id)

    topic_id = int(call.data.split(":")[1])
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


@bot.callback_query_handler(func=lambda call: call.data.startswith("reset:"))
def reset_progress_handler(call: CallbackQuery) -> None:
    topic_id = int(call.data.split(":")[1])
    user_id = call.from_user.id
    user = TelegramUser.objects.get(telegram_id=user_id)

    UserAnswer.objects.filter(user=user, question__category_id=topic_id).delete()
    UserCategoryProgress.objects.filter(user=user, category_id=topic_id).update(
        correct_count=0, total_answered=0
    )

    bot.answer_callback_query(call.id, "🔄 Full reset complete!")
    call.data = f"topic:{topic_id}"
    start_quiz(call)


@bot.callback_query_handler(func=lambda call: call.data.startswith("retry_fail:"))
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
            self.from_user = type("User", (), {"id": user_id, "first_name": first_name, "username": username})()
            self.chat = type("Chat", (), {"id": user_id})()

    msg = FakeMessage(call.from_user.id, call.from_user.first_name, call.from_user.username)
    handle_start(msg)
