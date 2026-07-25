from django.db import models

from properties.models import Property


class Inquiry(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiries",
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_resolved = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "inquiries"
        ordering = ["-created_at"]

    def __str__(self):
        target = self.property.title if self.property else "General inquiry"
        return f"{self.name} — {target}"
