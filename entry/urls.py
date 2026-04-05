from django.urls import path

from .views import (
    login_page,
    logout_view,
    parent_student_profile,
    principal_dashboard,
    student_cpp,
    student_cpp_gesp,
    student_cpp_gesp4,
    student_cpp_gesp4_array_2d,
    student_courses,
    teacher_students,
)


urlpatterns = [
    path("", login_page, name="login"),
    path("logout", logout_view, name="logout"),
    path("student/courses", student_courses, name="student-courses"),
    path("student/cpp", student_cpp, name="student-cpp"),
    path("student/cpp/gesp", student_cpp_gesp, name="student-cpp-gesp"),
    path("student/cpp/gesp/gesp4", student_cpp_gesp4, name="student-cpp-gesp4"),
    path("student/cpp/gesp/gesp4/array-2d", student_cpp_gesp4_array_2d, name="student-cpp-gesp4-array-2d"),
    path("parent/student-profile", parent_student_profile, name="parent-student-profile"),
    path("teacher/students", teacher_students, name="teacher-students"),
    path("principal/dashboard", principal_dashboard, name="principal-dashboard"),
]
