from django.shortcuts import render, redirect
from django.http import JsonResponse
from openai import OpenAI
import markdown2
from django.contrib import auth
from django.contrib.auth.models import User
from .models import Chat
from django.utils import timezone
from decouple import config

# OpenRouter client
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
     api_key=config("API_KEY"),  # ✅ Reads API_KEY from .env,
)

def ask_openai(message):
    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b:free",
            messages=[{"role": "user", "content": message}],
        )
        answer = completion.choices[0].message.content.strip()
        return answer
    except Exception as e:
        return f"Error: {str(e)}"

# Chatbot view (open for all, history only for logged in users)
def chatbot_view(request):
    chats = []
    if request.user.is_authenticated:
        chats = Chat.objects.filter(user=request.user).order_by("created_at")

    if request.method == 'POST':
        message = request.POST.get('message')
        response = ask_openai(message)

        # Convert Markdown → HTML
        formatted_response = markdown2.markdown(response)

        # Save chat only if user is logged in
        if request.user.is_authenticated:
            Chat.objects.create(
                user=request.user,
                message=message,
                response=response,
                created_at=timezone.now()
            )

        return JsonResponse({'message': message, 'response': formatted_response})

    return render(request, 'chatbot.html', {'chats': chats})

# Authentication views
def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = auth.authenticate(request, username=username, password=password)

        if user is not None:
            auth.login(request, user)
            return redirect('chatbot')
        else:
            error_message = 'Invalid Username or Password'
            return render(request, 'login.html', {'error_message': error_message})
    return render(request, 'login.html')

def register_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password1 = request.POST['password1']
        password2 = request.POST['password2']

        if password1 == password2:
            try:
                user = User.objects.create_user(username, email, password1)
                user.save()
                auth.login(request, user)
                return redirect('chatbot')
            except:
                error_message = 'Error creating account'
                return render(request, 'register.html', {'error_message': error_message})
        else:
            error_message = "Password doesn't match"
            return render(request, 'register.html', {'error_message': error_message})
    return render(request, 'register.html')

def logout_view(request):
    auth.logout(request)
    return redirect('chatbot')
