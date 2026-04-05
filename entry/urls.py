from django.urls import path

from .views import (
    login_page,
    logout_view,
    parent_student_profile,
    principal_dashboard,
    student_courses,
    teacher_students,
)


urlpatterns = [
    path("", login_page, name="login"),
    path("logout", logout_view, name="logout"),
    path("student/courses", student_courses, name="student-courses"),
    path("parent/student-profile", parent_student_profile, name="parent-student-profile"),
    path("teacher/students", teacher_students, name="teacher-students"),
    path("principal/dashboard", principal_dashboard, name="principal-dashboard"),
]
