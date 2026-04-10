from __future__ import annotations


GESP2_PHASE = "GESP2"
ENUMERATION_METHOD_CONTENT_SLUG = "enumeration-method"
ASCII_CHAR_ENCODING_CONTENT_SLUG = "ascii-char-encoding"

GESP2_KNOWLEDGE_DEFINITIONS = [
    {
        "slug": "enumeration-method",
        "order_label": "知识点 01",
        "title": "枚举法",
        "subtitle": "从候选答案中逐个尝试",
        "summary": "GESP2 枚举法真实教学页，围绕候选空间、边界判断、易错点和真题例子展开。",
        "route_path": "/student/cpp/gesp/gesp2/enumeration-method",
        "content_mode": "real",
    },
    {
        "slug": "branch-structure",
        "order_label": "知识点 02",
        "title": "分支结构",
        "subtitle": "条件判断与路径选择",
        "summary": "GESP2 分支结构已纳入知识点目录，当前先用预留页承接。",
        "route_path": "/student/cpp/gesp/gesp2/branch-structure",
        "content_mode": "reserved",
    },
    {
        "slug": "loop-structure",
        "order_label": "知识点 03",
        "title": "循环结构",
        "subtitle": "重复执行与终止条件",
        "summary": "GESP2 循环结构已纳入知识点目录，当前先用预留页承接。",
        "route_path": "/student/cpp/gesp/gesp2/loop-structure",
        "content_mode": "reserved",
    },
    {
        "slug": "basic-simulation",
        "order_label": "知识点 04",
        "title": "简单模拟",
        "subtitle": "按规则逐步实现过程",
        "summary": "GESP2 简单模拟已纳入知识点目录，当前先用预留页承接。",
        "route_path": "/student/cpp/gesp/gesp2/basic-simulation",
        "content_mode": "reserved",
    },
    {
        "slug": "string-basics",
        "order_label": "知识点 05",
        "title": "字符串基础",
        "subtitle": "字符读取与基础处理",
        "summary": "GESP2 字符串基础已纳入知识点目录，当前先用预留页承接。",
        "route_path": "/student/cpp/gesp/gesp2/string-basics",
        "content_mode": "reserved",
    },
    {
        "slug": "ascii-char-encoding",
        "order_label": "知识点 06",
        "title": "ASCII 编码",
        "subtitle": "字符与编码值的对应关系",
        "summary": "GESP2 ASCII 编码知识点页，围绕字符与整数、字符区间判断、大小写偏移和典型真题展开。",
        "route_path": "/student/cpp/gesp/gesp2/ascii-char-encoding",
        "content_mode": "real",
    },
]

GESP2_KNOWLEDGE_MAP = {item["slug"]: item for item in GESP2_KNOWLEDGE_DEFINITIONS}
GESP2_KNOWLEDGE_SLUGS = [item["slug"] for item in GESP2_KNOWLEDGE_DEFINITIONS]
