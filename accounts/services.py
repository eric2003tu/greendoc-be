"""Account rules: validation, guest-data transfer, deletion."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from core.http import ApiError
from documents.models import Document
from workspace.models import Preferences, Source, SourcesSeeded

from .models import Profile

User = get_user_model()
NAME_MAX = 50


def clean_name(value, field, label):
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f"Enter your {label}.", code="invalid_field", fields={field: f"Enter your {label}."})
    value = " ".join(value.split())
    if len(value) > NAME_MAX:
        msg = f"{label.capitalize()} can be at most {NAME_MAX} characters."
        raise ApiError(msg, code="invalid_field", fields={field: msg})
    return value


def clean_email(value):
    if not isinstance(value, str) or not value.strip():
        raise ApiError("Enter your email address.", code="invalid_field", fields={"email": "Enter your email address."})
    value = value.strip().lower()
    try:
        if len(value) > 254:
            raise ValidationError("too long")
        validate_email(value)
    except ValidationError:
        raise ApiError(
            "Enter a valid email address.", code="invalid_field", fields={"email": "Enter a valid email address."}
        )
    return value


def clean_password(value, user=None, field="password"):
    if not isinstance(value, str) or not value:
        raise ApiError("Enter a password.", code="invalid_field", fields={field: ["Enter a password."]})
    if len(value) > 128:
        raise ApiError("Password is too long.", code="invalid_field", fields={field: ["Password is too long."]})
    try:
        validate_password(value, user)
    except ValidationError as exc:
        messages = list(exc.messages)
        raise ApiError(messages[0], code="weak_password", fields={field: messages})
    return value


def display_name(user):
    return f"{user.first_name} {user.last_name}".strip()


def initials(user):
    return (user.first_name[:1] + user.last_name[:1]).upper() or "U"


def user_json(user):
    return {
        "id": user.profile.owner_id.hex,
        "email": user.email,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "displayName": display_name(user),
        "initials": initials(user),
        "createdAt": user.date_joined.isoformat(),
        "documentCount": Document.objects.filter(client_id=user.profile.owner_id).count(),
    }


@transaction.atomic
def create_user(email, first_name, last_name, password):
    user = User(username=email, email=email, first_name=first_name, last_name=last_name)
    user.set_password(password)
    user.save()
    Profile.objects.create(user=user)
    return user


@transaction.atomic
def claim_guest_data(guest_id, owner_id):
    """Move everything a browser created as a guest into the account, so signing
    in never loses work. Existing account data wins on conflicts."""
    if guest_id == owner_id:
        return {"documents": 0, "sources": 0, "preferences": False}
    documents = Document.objects.filter(client_id=guest_id).update(client_id=owner_id)

    have = set(Source.objects.filter(client_id=owner_id).values_list("tag", flat=True))
    moved = 0
    for source in Source.objects.filter(client_id=guest_id):
        if source.tag in have:
            source.delete()
        else:
            source.client_id = owner_id
            source.save(update_fields=["client_id"])
            moved += 1
    if SourcesSeeded.objects.filter(client_id=guest_id).exists():
        SourcesSeeded.objects.get_or_create(client_id=owner_id)
        SourcesSeeded.objects.filter(client_id=guest_id).delete()

    pref_moved = False
    guest_pref = Preferences.objects.filter(client_id=guest_id).first()
    if guest_pref:
        mine = Preferences.objects.filter(client_id=owner_id).first()
        if mine is None:
            guest_pref.client_id = owner_id
            guest_pref.save(update_fields=["client_id"])
            pref_moved = True
        else:
            mine.data = {**guest_pref.data, **mine.data}
            mine.save()
            guest_pref.delete()
    return {"documents": documents, "sources": moved, "preferences": pref_moved}


@transaction.atomic
def delete_account(user):
    owner = user.profile.owner_id
    Document.objects.filter(client_id=owner).delete()  # cascades comments, versions, merge data
    Source.objects.filter(client_id=owner).delete()
    SourcesSeeded.objects.filter(client_id=owner).delete()
    Preferences.objects.filter(client_id=owner).delete()
    user.delete()
