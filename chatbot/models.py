from django.db import models
from django.contrib.auth.models import User
import uuid

# Existing Chat model (keeping for backward compatibility)
class Chat(models.Model):   
    user = models.ForeignKey(User, on_delete=models.CASCADE)  
    message = models.TextField()  
    response = models.TextField() 
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user.username}: {self.message}'


# 🆕 New model for conversation sessions/threads
class ChatSession(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-updated_at']
    
    def __str__(self):
        return f'Session {self.session_id} - {self.user.username}'


# 🆕 New model for individual messages within sessions
class Message(models.Model):
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('system', 'System'),
    ]
    
    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp']
    
    def __str__(self):
        return f'{self.role}: {self.content[:50]}...'


# 🆕 New model for uploaded PDFs
class UploadedPDF(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)  
    file = models.FileField(upload_to="pdfs/")   # stored in MEDIA_ROOT/pdfs/
    uploaded_at = models.DateTimeField(auto_now_add=True)
    faiss_index_path = models.CharField(max_length=255, blank=True, null=True)  
    # optional: store path to FAISS index for this PDF

    def __str__(self):
        return f'{self.user.username} - {self.file.name}'
