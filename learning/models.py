from django.db import models
from django.utils import timezone


class LearningProfile(models.Model):
    """What GreenDoc has learned about one writer (the on-device model, backed up here).

    `data` holds only derived counts (words, names, corrections, list style, three-word
    phrases), never documents or sentences, and is private to its owner. `shared` is the
    writer's opt-in to also feed the shared engine. `contributed` remembers how much of
    `data` the engine has already received, so each upload adds only what is new.
    """

    client_id = models.UUIDField(unique=True)
    shared = models.BooleanField(default=False)
    data = models.JSONField(default=dict)
    contributed = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class TermStat(models.Model):
    """The shared engine: everything learned from everyone, with no record of who taught it.

    One row per word, name, correction or phrase. Rows only grow. `contributors` counts how
    many different people have used the term enough to establish it (each person counts once),
    which is what decides when it joins the shared dictionary.
    """

    WORD, NAME, FIX, NGRAM = "word", "name", "fix", "ngram"
    KINDS = [(WORD, "Word"), (NAME, "Name"), (FIX, "Correction"), (NGRAM, "Phrase")]

    kind = models.CharField(max_length=8, choices=KINDS)
    key = models.CharField(max_length=80)
    count = models.PositiveBigIntegerField(default=0)
    contributors = models.PositiveIntegerField(default=0)
    # An administrator took it out of the dictionary: it keeps counting but is never published.
    removed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "key"], name="unique_term")]
        indexes = [models.Index(fields=["kind", "-contributors", "-count"])]

    def __str__(self):
        return f"{self.kind}: {self.key} ({self.contributors} people, {self.count} uses)"


class EngineDay(models.Model):
    """One row per day of engine activity, so the admin can see how it grows. Counts only."""

    day = models.DateField(unique=True)
    uploads = models.PositiveIntegerField(default=0)
    new_terms = models.PositiveIntegerField(default=0)
    uses_added = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["-day"]
