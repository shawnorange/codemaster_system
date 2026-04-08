from django.urls import path

from .views import (
    login_page,
    logout_view,
    parent_student_profile,
    principal_dashboard,
    student_account_settings,
    student_cpp,
    student_cpp_gesp,
    student_cpp_gesp4,
    student_cpp_gesp4_array_2d,
    student_cpp_gesp4_topic,
    student_courses,
    teacher_course_detail,
    teacher_student_detail,
    teacher_students,
)


urlpatterns = [
    path("", login_page, name="login"),
    path("logout", logout_view, name="logout"),
    path("student/courses", student_courses, name="student-courses"),
    path("student/account-settings", student_account_settings, name="student-account-settings"),
    path("student/cpp", student_cpp, name="student-cpp"),
    path("student/cpp/gesp", student_cpp_gesp, name="student-cpp-gesp"),
    path("student/cpp/gesp/gesp4", student_cpp_gesp4, name="student-cpp-gesp4"),
    path("student/cpp/gesp/gesp4/array-2d", student_cpp_gesp4_array_2d, name="student-cpp-gesp4-array-2d"),
    path("student/cpp/gesp/gesp4/<slug:topic_slug>", student_cpp_gesp4_topic, name="student-cpp-gesp4-topic"),
    path("parent/student-profile", parent_student_profile, name="parent-student-profile"),
    path("teacher/students", teacher_students, name="teacher-students"),
    path("teacher/courses/<slug:course_slug>", teacher_course_detail, name="teacher-course-detail"),
    path("teacher/students/<int:student_id>", teacher_student_detail, name="teacher-student-detail"),
    path("principal/dashboard", principal_dashboard, name="principal-dashboard"),
]
