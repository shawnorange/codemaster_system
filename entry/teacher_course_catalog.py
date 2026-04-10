TEACHER_COURSE_DEFINITIONS = [
    {
        "slug": "scratch",
        "level": "L1-L3",
        "title": "Scratch",
        "summary": "图形化编程与创意表达主线，当前先保留教师课程入口。",
        "state": "reserved",
        "detail_title": "Scratch 课程分类页",
        "detail_description": "当前先承接教师端的最小课程入口，后续可在这里继续拆分项目课、进阶课和班级安排。",
        "category_items": [
            {
                "title": "创意搭建",
                "description": "围绕动画、故事和基础交互任务展开的课程分类入口。",
                "status_text": "内容预留",
            },
            {
                "title": "逻辑训练",
                "description": "用于承接 Scratch 逻辑任务、闯关课和课堂练习分类。",
                "status_text": "内容预留",
            },
        ],
    },
    {
        "slug": "pbl",
        "level": "P1-P3",
        "title": "PBL",
        "summary": "项目制学习与综合表达方向，当前先保留课程分类壳层。",
        "state": "reserved",
        "detail_title": "PBL 课程分类页",
        "detail_description": "当前只建立最小课程入口，后续可继续承接主题项目、展示任务和跨学科活动。",
        "category_items": [
            {
                "title": "主题项目",
                "description": "按项目主题组织的课程分类入口。",
                "status_text": "内容预留",
            },
            {
                "title": "表达任务",
                "description": "用于承接成果展示、讲解和复盘任务的入口。",
                "status_text": "内容预留",
            },
        ],
    },
    {
        "slug": "cpp",
        "level": "C1-C4",
        "title": "C++",
        "summary": "算法与竞赛方向，当前已接入 GESP2 枚举法知识点页和 GESP4 多专题内容链路。",
        "state": "active",
        "detail_title": "C++ 课程分类页",
        "detail_description": "这里承接 C++ 课程下的分类入口。当前先建立 GESP、CSP、机器人编程 3 个最小分类入口。",
        "category_items": [
            {
                "title": "GESP",
                "description": "当前已接入 GESP2 知识点目录与 GESP4 多专题框架，后续可继续把更多知识点页接进来。",
                "status_text": "已接入",
            },
            {
                "title": "CSP",
                "description": "后续承接 CSP 算法专题、题单训练和阶段任务入口。",
                "status_text": "内容预留",
            },
            {
                "title": "机器人编程",
                "description": "作为 C++ 工程实践与控制类内容的分类入口预留。",
                "status_text": "内容预留",
            },
        ],
    },
    {
        "slug": "uav",
        "level": "S1-S3",
        "title": "无人机",
        "summary": "设备实践与控制逻辑方向，当前先保留教师课程入口。",
        "state": "reserved",
        "detail_title": "无人机课程分类页",
        "detail_description": "当前先保留分类入口壳层，后续可承接飞行训练、控制逻辑和设备任务。",
        "category_items": [
            {
                "title": "飞行训练",
                "description": "用于承接基础飞行、路线规划和安全训练。",
                "status_text": "内容预留",
            },
            {
                "title": "控制逻辑",
                "description": "用于承接控制逻辑、任务执行和设备调试。",
                "status_text": "内容预留",
            },
        ],
    },
]

TEACHER_COURSE_MAP = {item["slug"]: item for item in TEACHER_COURSE_DEFINITIONS}
