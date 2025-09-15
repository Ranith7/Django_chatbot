from django.db import models
from django.contrib.auth.models import User

# Existing Chat model
class Chat(models.Model):   
    user = models.ForeignKey(User, on_delete=models.CASCADE)  
    message = models.TextField()  
    response = models.TextField() 
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user.username}: {self.message}'


# 🆕 New model for uploaded PDFs
class UploadedPDF(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)  
    file = models.FileField(upload_to="pdfs/")   # stored in MEDIA_ROOT/pdfs/
    uploaded_at = models.DateTimeField(auto_now_add=True)
    faiss_index_path = models.CharField(max_length=255, blank=True, null=True)  
    # optional: store path to FAISS index for this PDF

    def __str__(self):
        return f'{self.user.username} - {self.file.name}'
