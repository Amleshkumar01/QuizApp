"""
URL configuration for student_reg project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf.urls.static import static
from django.conf import settings
from django.urls import path
from app1 import views

urlpatterns = [
    
    # Quiz management for admin

    path('admin/quizzes/', views.admin_manage_quizzes, name='admin_manage_quizzes'),
    path('admin/quizzes/add/', views.admin_add_quiz, name='admin_add_quiz'),
    path('admin/quizzes/edit/<int:quiz_id>/', views.admin_edit_quiz, name='admin_edit_quiz'),
    path('admin/quizzes/delete/<int:quiz_id>/', views.admin_delete_quiz, name='admin_delete_quiz'),
    path('admin/quizzes/upload_csv/', views.upload_quizzes_csv, name='upload_quizzes_csv'),


    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    
    # default admin page
    path('admin/', admin.site.urls),
    
    path('',views.home, name='home'),
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('category/<int:category_id>/', views.category_quizzes, name='category_quizzes'),   
    path('quiz/<int:quiz_id>/start/', views.start_quiz, name='start_quiz'),
   path('quiz/attempt/<int:quiz_id>/', views.attempt_quiz, name='attempt_quiz'),

    path('quiz/result/', views.quiz_result, name='quiz_result'),
    path('my-attempts/', views.my_attempts, name='my_attempts'),
    
   

    path('admin/users/', views.admin_manage_users, name='admin_manage_users'),
    path('admin/users/add/', views.admin_add_user, name='admin_add_user'),
    path('admin/users/edit/<int:user_id>/', views.edit_user, name='edit_user'),
    path('admin/users/upload_csv/', views.upload_users_csv, name='upload_users_csv'),
    path('admin/users/delete/<int:user_id>/', views.delete_user, name='delete_user'),
    path('admin_manage_users/', views.admin_manage_users, name='admin_manage_users'),


]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)