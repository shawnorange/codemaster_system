from django.db import migrations


LEGACY_CODE = "gesp2-ascii-2023-03-sc13"
CORRECT_CODE = "gesp2-ascii-2023-03-sc14"
TARGET_SLUG = "ascii-char-encoding"


CORRECT_CODE_TEXT = (
    "#include <iostream>\n"
    "using namespace std;\n"
    "int main() {\n"
    "    int cnt = 0;\n"
    "    for (char ch = '1'; ch <= '9'; ch++)\n"
    "        if (__________)\n"
    "            cnt++;\n"
    "    cout << cnt << endl;\n"
    "    return 0;\n"
    "}"
)


def _apply_correct_payload(payload: dict) -> dict:
    next_payload = dict(payload or {})
    next_payload.update(
        {
            "statement": "在下列代码的横线处填写一项，可以使得输出是 5。",
            "code": CORRECT_CODE_TEXT,
            "answer_label": "D",
            "answer_analysis": "字符 '1' 到 '9' 的 ASCII 码依次是 49 到 57，奇数编码一共对应 5 个字符，因此应填 ch % 2 == 1。",
        }
    )
    return next_payload


def fix_ascii_202303_question14(apps, schema_editor):
    Question = apps.get_model("entry", "Question")

    legacy_question = (
        Question.objects.filter(code=LEGACY_CODE, content_slug=TARGET_SLUG)
        .order_by("id")
        .first()
    )
    current_question = (
        Question.objects.filter(code=CORRECT_CODE, content_slug=TARGET_SLUG)
        .order_by("id")
        .first()
    )

    if current_question:
        current_question.source_question_no = 14
        current_question.payload = _apply_correct_payload(current_question.payload)
        current_question.save(update_fields=["source_question_no", "payload"])
        if legacy_question and legacy_question.id != current_question.id:
            legacy_question.delete()
        return

    if not legacy_question:
        return

    legacy_question.code = CORRECT_CODE
    legacy_question.source_question_no = 14
    legacy_question.payload = _apply_correct_payload(legacy_question.payload)
    legacy_question.save(update_fields=["code", "source_question_no", "payload"])


def rollback_ascii_202303_question14(apps, schema_editor):
    Question = apps.get_model("entry", "Question")

    question = (
        Question.objects.filter(code=CORRECT_CODE, content_slug=TARGET_SLUG)
        .order_by("id")
        .first()
    )
    if not question:
        return

    payload = dict(question.payload or {})
    payload.update(
        {
            "statement": "在代码横线处填写一项，使输出为 50 10。",
            "code": "",
            "answer_label": "C",
            "answer_analysis": "本题考字符与字符比较，本质依赖编码顺序。",
        }
    )
    question.code = LEGACY_CODE
    question.source_question_no = 13
    question.payload = payload
    question.save(update_fields=["code", "source_question_no", "payload"])


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0010_seed_gesp2_ascii_topic"),
    ]

    operations = [
        migrations.RunPython(fix_ascii_202303_question14, rollback_ascii_202303_question14),
    ]
