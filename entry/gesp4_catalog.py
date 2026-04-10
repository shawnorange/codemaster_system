from __future__ import annotations


GESP4_PHASE = "GESP4"
ARRAY_2D_CONTENT_SLUG = "array-2d"
BINARY_SEARCH_CONTENT_SLUG = "binary-search"
SORTING_CONTENT_SLUG = "sorting"
STRINGS_CONTENT_SLUG = "strings"

GESP4_TOPIC_DEFINITIONS = [
    {
        "slug": "array-2d",
        "order_label": "专题 01",
        "title": "二维数组专题",
        "subtitle": "矩阵遍历与二维存储",
        "summary": "GESP4 二维数组专题真实内容入口，已接入专题首页和讲次内容。",
        "route_path": "/student/cpp/gesp/gesp4/array-2d",
        "content_mode": "real",
    },
    {
        "slug": BINARY_SEARCH_CONTENT_SLUG,
        "order_label": "专题 02",
        "title": "二分查找专题",
        "subtitle": "检索与区间缩减",
        "summary": "GESP4 二分查找专题已纳入内容开放体系，当前先用内容预留页承载。",
        "route_path": "/student/cpp/gesp/gesp4/binary-search",
        "content_mode": "reserved",
    },
    {
        "slug": SORTING_CONTENT_SLUG,
        "order_label": "专题 03",
        "title": "排序专题",
        "subtitle": "排序与比较策略",
        "summary": "GESP4 排序专题已纳入内容开放体系，当前先用内容预留页承载。",
        "route_path": "/student/cpp/gesp/gesp4/sorting",
        "content_mode": "reserved",
    },
    {
        "slug": "enumeration-simulation",
        "order_label": "专题 04",
        "title": "枚举与模拟专题",
        "subtitle": "遍历实现与状态模拟",
        "summary": "GESP4 枚举与模拟专题已纳入内容开放体系，当前先用内容预留页承载。",
        "route_path": "/student/cpp/gesp/gesp4/enumeration-simulation",
        "content_mode": "reserved",
    },
    {
        "slug": STRINGS_CONTENT_SLUG,
        "order_label": "专题 05",
        "title": "字符串专题",
        "subtitle": "文本处理与字符分析",
        "summary": "GESP4 字符串专题已纳入内容开放体系，当前先用内容预留页承载。",
        "route_path": "/student/cpp/gesp/gesp4/strings",
        "content_mode": "reserved",
    },
    {
        "slug": "algorithm-foundation",
        "order_label": "专题 06",
        "title": "基础算法综合专题",
        "subtitle": "综合训练与基础算法整合",
        "summary": "GESP4 基础算法综合专题已纳入内容开放体系，当前先用内容预留页承载。",
        "route_path": "/student/cpp/gesp/gesp4/algorithm-foundation",
        "content_mode": "reserved",
    },
]

GESP4_TOPIC_MAP = {topic["slug"]: topic for topic in GESP4_TOPIC_DEFINITIONS}
GESP4_TOPIC_SLUGS = [topic["slug"] for topic in GESP4_TOPIC_DEFINITIONS]
