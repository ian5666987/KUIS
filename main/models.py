# Models seem to connect to database

from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
import re

# Create your models here.
class Document(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    # Preprocessed
    token_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'uploaded_at']),
        ]

    def __str__(self):
        return self.title
    
class Token(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='tokens')
    word = models.CharField(max_length=100)
    position = models.IntegerField()

    class Meta:
        indexes = [
            models.Index(fields=['word']),
            models.Index(fields=['document', 'position']),
        ]

    def __str__(self):
        return f"{self.word} ({self.document_id}:{self.position})"
    
def tokenize_text(text):
    # simple clean tokenizer (we will improve later if needed)
    return re.findall(r"\b\w+\b", text.lower())

#Signals (kind of callback), must be placed at the bottom of (Model) files
@receiver(post_save, sender=Document)
def create_tokens(sender, instance, created, **kwargs):
    if not created:
        return

    words = tokenize_text(instance.content)

    tokens = [
        Token(
            document=instance,
            word=word,
            position=i
        )
        for i, word in enumerate(words)
    ]

    Token.objects.bulk_create(tokens)

    # update token count
    instance.token_count = len(words)
    instance.save(update_fields=['token_count'])