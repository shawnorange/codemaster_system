from django.db import migrations


SC14_CODE = "gesp2-ascii-2023-03-sc14"
SC13_CODE = "gesp2-ascii-2023-06-sc13"
TARGET_SLUG = "ascii-char-encoding"

SC14_CODE_TEXT = (
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

SC13_CODE_TEXT = (
    "#include <iostream>\n"
    "using namespace std;\n"
    "int main() {\n"
    "    char a = '3', b = '6';\n"
    "    cout << ________ << endl;\n"
    "    return 0;\n"
    "}"
)

SC13_ANALYSIS = (
    "a、b 分别是数字字符 '3' 和 '6'，先相加得到两个编码值之和；"
    "减去一个 '0' 后得到字符 '9' 的编码值 57，再转回 char 才会输出字符 9。"
)


def _update_payload(payload: dict, *, code_text: str, answer_label: str | None = None, answer_analysis: str | None = None) -> dict:
    next_payload = dict(payload or {})
    next_payload["code"] = code_text
    if answer_label is not None:
        next_payload["answer_label"] = answer_label
    if answer_analysis is not None:
        next_payload["answer_analysis"] = answer_analysis
    return next_payload


def fix_ascii_code_blocks(apps, schema_editor):
    Question = apps.get_model("entry", "Question")

    sc14 = Question.objects.filter(code=SC14_CODE, content_slug=TARGET_SLUG).first()
    if sc14:
        sc14.payload = _update_payload(sc14.payload, code_text=SC14_CODE_TEXT)
        sc14.save(update_fields=["payload"])

    sc13 = Question.objects.filter(code=SC13_CODE, content_slug=TARGET_SLUG).first()
    if sc13:
        sc13.payload = _update_payload(
            sc13.payload,
            code_text=SC13_CODE_TEXT,
            answer_label="D",
            answer_analysis=SC13_ANALYSIS,
        )
        sc13.save(update_fields=["payload"])


def rollback_ascii_code_blocks(apps, schema_editor):
    Question = apps.get_model("entry", "Question")

    sc13 = Question.objects.filter(code=SC13_CODE, content_slug=TARGET_SLUG).first()
    if sc13:
        payload = dict(sc13.payload or {})
        payload.update(
            {
                "code": "",
                "answer_label": "B",
                "answer_analysis": "若 a、b 是数字字符，直接相加得到的是两个编码值之和，要减掉一个 '0'。",
            }
        )
        sc13.payload = payload
        sc13.save(update_fields=["payload"])


class Migration(migrations.Migration):

    dependencies = [
        ("entry", "0011_fix_ascii_202303_question14"),
    ]

    operations = [
        migrations.RunPython(fix_ascii_code_blocks, rollback_ascii_code_blocks),
    ]
