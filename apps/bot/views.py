import telebot
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from .bot import bot


@csrf_exempt
def telegram_webhook(request):
    if request.method == "POST":
        json_string = request.body.decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return HttpResponse('OK')
    return HttpResponse('Bot Active')
