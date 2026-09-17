# Models seem to connect to database

from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .corpus_parsing import extract_word_streams

# Create your models here.
class Document(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    # Preprocessed
    token_count = models.IntegerField(default=0)
    token_count_corrected = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'uploaded_at']),
        ]

    def __str__(self):
        return self.title
    
class Corpus(models.Model):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    documents = models.ManyToManyField(Document, related_name='corpora', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'corpora'
        ordering = ['name']

    def __str__(self):
        return self.name


class Token(models.Model):
    ORIGINAL = 'original'
    CORRECTED = 'corrected'
    MODE_CHOICES = [
        (ORIGINAL, 'Original'),
        (CORRECTED, 'Corrected'),
    ]

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='tokens')
    word = models.CharField(max_length=100)
    position = models.IntegerField()
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=ORIGINAL)

    class Meta:
        indexes = [
            models.Index(fields=['word']),
            models.Index(fields=['document', 'mode', 'position']),
        ]

    def __str__(self):
        return f"{self.word} ({self.document_id}:{self.mode}:{self.position})"


def build_tokens(document):
    """(Re)computes the Token rows for a document from its content, for both the
    original and corrected word streams. Does not save the document itself."""
    original_words, corrected_words = extract_word_streams(document.content)

    tokens = [
        Token(document=document, word=word, position=i, mode=Token.ORIGINAL)
        for i, word in enumerate(original_words)
    ] + [
        Token(document=document, word=word, position=i, mode=Token.CORRECTED)
        for i, word in enumerate(corrected_words)
    ]

    Token.objects.bulk_create(tokens)

    return len(original_words), len(corrected_words)


#Signals (kind of callback), must be placed at the bottom of (Model) files
@receiver(post_save, sender=Document)
def create_tokens(sender, instance, created, **kwargs):
    if not created:
        return

    token_count, token_count_corrected = build_tokens(instance)

    instance.token_count = token_count
    instance.token_count_corrected = token_count_corrected
    instance.save(update_fields=['token_count', 'token_count_corrected'])