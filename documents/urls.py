from django.urls import path

from . import views, views_review, views_share, views_tools

D = "documents/<uuid:doc_id>/"

urlpatterns = [
    path("documents/", views.document_collection),
    path("documents/import/", views.document_import),
    path(D, views.document_detail),
    path(D + "duplicate/", views.document_duplicate),
    path(D + "share/", views.document_share),
    path(D + "invite/", views_share.document_invite),
    path(D + "email-copy/", views_share.document_email_copy),
    path("shared/<str:token>/", views.shared_document),
    path("recent/", views.recent),
    # tools
    path(D + "stats/", views_tools.document_stats),
    path(D + "toc/", views_tools.document_toc),
    path(D + "find/", views_tools.document_find),
    path(D + "spellcheck/", views_tools.document_spellcheck),
    path(D + "readability/", views_tools.document_readability),
    path(D + "export/", views_tools.document_export),
    # version history
    path(D + "versions/", views_tools.version_collection),
    path(D + "versions/<uuid:version_id>/", views_tools.version_detail),
    path(D + "versions/<uuid:version_id>/restore/", views_tools.version_restore),
    # mail merge
    path(D + "mailmerge/", views_tools.mailmerge),
    path(D + "mailmerge/preview/", views_tools.mailmerge_preview),
    path(D + "mailmerge/finish/", views_tools.mailmerge_finish),
    # review: comments
    path(D + "comments/", views_review.comment_collection),
    path(D + "comments/<str:cid>/", views_review.comment_detail),
    path(D + "comments/<str:cid>/replies/", views_review.comment_reply),
    path(D + "comments/<str:cid>/replies/<str:rid>/", views_review.comment_reply_detail),
    # review: track changes
    path(D + "revisions/", views_review.revision_collection),
    path(D + "revisions/accept-all/", views_review.revisions_accept_all),
    path(D + "revisions/reject-all/", views_review.revisions_reject_all),
    path(D + "revisions/<str:rid>/", views_review.revision_detail),
]
