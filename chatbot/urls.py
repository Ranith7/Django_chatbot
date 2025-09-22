from django.urls import path
from . import views

urlpatterns = [
    path('', views.chatbot_view, name="chatbot"),
    path('login/', views.login_view, name="login"),
    path('register/', views.register_view, name="register"),
    path('logout/', views.logout_view, name="logout"),

    # ✅ New route for PDF upload
    path('upload-pdf/', views.upload_pdf, name="upload_pdf"),
    # ✅ Test route for debugging PDF processing
    path('test-pdf/', views.test_pdf_processing, name="test_pdf"),
    # ✅ Debug route for CSRF issues
    path('debug-csrf/', views.debug_csrf, name="debug_csrf"),
    # ✅ New route for starting new conversation session
    path('new-session/', views.start_new_session, name="new_session"),
]





