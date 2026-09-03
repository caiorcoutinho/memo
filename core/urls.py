from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),

    # Sistema de memos
    path('memo/', views.memo_home, name='memo_home'),
    path('memo/timeline/', views.timeline, name='timeline'),
    path('memo/upload/', views.photo_upload, name='photo_upload'),
    path('memo/users/search/', views.user_search, name='user_search'),
    path('memo/new/', views.memo_create, name='memo_create'),
    path('memo/photo/<int:pk>/', views.photo_detail, name='photo_detail'),
    path('memo/photo/<int:pk>/edit/', views.photo_edit, name='photo_edit'),
    path('memo/photo/<int:pk>/delete/', views.photo_delete, name='photo_delete'),
    path('memo/photo/<int:pk>/share/', views.photo_share, name='photo_share'),
    path('memo/<int:pk>/', views.memo_detail, name='memo_detail'),
    path('memo/<int:pk>/edit/', views.memo_edit, name='memo_edit'),
    path('memo/<int:pk>/delete/', views.memo_delete, name='memo_delete'),
    path('memo/<int:pk>/share/', views.memo_share, name='memo_share'),

    # Links públicos (sem login): o token é a credencial.
    path('l/<str:token>/', views.public_share, name='public_share'),
    path('l/<str:token>/foto/<int:pk>/', views.public_share_photo,
         name='public_share_photo'),

    # Autenticação simples
    path('register/', views.register, name='register'),
]
