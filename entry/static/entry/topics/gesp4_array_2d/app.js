const typeLabels = {
  single_choice: "单选题",
  judgement: "判断题",
  programming: "编程题",
};

const statusLabels = {
  success: "已抽取原卷内容",
  partial: "解析不稳定，先展示摘要",
  manual: "手工补全摘要",
};

const topicAppRoot = document.querySelector(".gesp4-array-topic");
const questionTemplate = document.getElementById("question-card-template");
const TEACHER_MODE_STORAGE_KEY = "gesp4-teacher-mode";
const animationState = {
  runtimes: new Map(),
  activeModuleId: null,
};
const teacherState = {
  enabled: false,
  focusedModuleId: null,
  currentQuestionId: null,
  currentLectureId: null,
};

const lectureOneTraversalValues = [
  [5, 1, 4],
  [2, 8, 6],
];

const lectureTwoMatrixValues = [
  [1, 2, 3, 4],
  [5, 6, 7, 8],
  [9, 10, 11, 12],
];

const lectureThreeStatsValues = [
  [3, 1, 6],
  [2, 8, 4],
  [7, 5, 9],
];

const lectureThreeWindowValues = [
  [1, 3, 2, 4],
  [5, 6, 1, 2],
  [7, 2, 8, 3],
  [4, 1, 5, 9],
];

const lectureFourInputRows = [".#..", "A..#", "#*B."];
const lectureFourDirectionRows = [".U..", "LCR.", ".D.."];
const lectureFourCropRows = ["ABCDE", "FGHIJ", "KLMNO", "PQRST"];
const lectureFiveDirectionRows = ["ABC", "DXE", "FGH"];
const lectureFiveSubrectRows = ["ABCDE", "FGHIJ", "KLMNO", "PQRST", "UVWXY"];
const lectureFiveCheckRows = ["....", ".AB.", ".CD.", "...."];
const lectureFiveCheckPatternRows = ["AB", "CX"];
const lectureSixHorizontalMatrix = [
  [1, 2, 3, 4],
  [5, 6, 7, 8],
  [9, 10, 11, 12],
];
const lectureSixVerticalMatrix = [
  [1, 2, 3],
  [4, 5, 6],
  [7, 8, 9],
  [10, 11, 12],
];
const lectureSixTransposeMatrix = [
  [1, 2, 3],
  [4, 5, 6],
  [7, 8, 9],
];
const lectureSixRowSwapMatrix = [
  [1, 2, 3, 4],
  [5, 6, 7, 8],
  [9, 10, 11, 12],
];
const lectureSixColSwapMatrix = [
  [1, 2, 3, 4],
  [5, 6, 7, 8],
  [9, 10, 11, 12],
  [13, 14, 15, 16],
];
const lectureSixLinearMatrix = [
  [11, 12, 13, 14],
  [21, 22, 23, 24],
  [31, 32, 33, 34],
];

const animationSuitesByChildId = {
  "lecture-1-template-animation": {
    suiteId: "lecture-1-suite",
    intro:
      "点击任意模块即可激活键盘控制。空格执行下一步；按钮支持上一步和重置。第 1 讲现在已经区分讲解区、动画区、debug 区和代码区。",
    modules: [
      {
        id: "lecture-1-memory-open",
        title: "二维数组内存开辟动画",
        description: "把 `int a[3][4];` 拆成 12 个按行连续开辟的单元，并同步展示二维网格与线性内存条。",
        maxStep: 12,
        getSnapshot: buildLectureOneMemorySnapshot,
      },
      {
        id: "lecture-1-index-visit",
        title: "下标访问动画",
        description: "用多个访问样例反复强调“先行后列”，把表达式、题面位置和高亮格子对应起来。",
        maxStep: 4,
        getSnapshot: buildLectureOneAccessSnapshot,
      },
      {
        id: "lecture-1-io-traversal",
        title: "输入输出遍历动画",
        description: "双重循环按步推进，显示当前执行行、i/j、访问格子和简易 debug 面板。",
        maxStep: 12,
        getSnapshot: buildLectureOneTraversalSnapshot,
      },
    ],
  },
  "lecture-2-template-animation": {
    suiteId: "lecture-2-suite",
    intro:
      "第 2 讲先落第一版三组动画：按行存储、一整行概念、函数传参图解。它们与第 1 讲共用同一套播放器、按钮和空格推进逻辑。",
    modules: [
      {
        id: "lecture-2-row-major",
        title: "按行存储动画",
        description: "同步高亮二维网格与一维线性内存，建立 row-major 的具体映射。",
        maxStep: 12,
        getSnapshot: buildLectureTwoRowMajorSnapshot,
      },
      {
        id: "lecture-2-whole-row",
        title: "一整行概念动画",
        description: "依次选中 `a[0]`、`a[1]`、`a[2]`，突出“a[i] 表示一整行”的含义。",
        maxStep: 3,
        getSnapshot: buildLectureTwoWholeRowSnapshot,
      },
      {
        id: "lecture-2-function-param",
        title: "二维数组函数传参图解",
        description: "通过图示说明为什么列数必须明确，以及 `arr[][4]` / `(*arr)[4]` 的含义。",
        maxStep: 4,
        getSnapshot: buildLectureTwoFunctionParamSnapshot,
      },
    ],
  },
  "lecture-3-template-animation": {
    suiteId: "lecture-3-suite",
    intro:
      "第 3 讲进入真正的数据处理。总和、最值、计数和固定窗口都继续复用同一套 step controller，每一步都会同步显示坐标、当前值、统计变量和代码执行行。",
    modules: [
      {
        id: "lecture-3-sum-accumulate",
        title: "总和累加动画",
        description: "逐格访问二维数组，实时展示 sum 的旧值、新值和当前累加动作。",
        maxStep: 9,
        getSnapshot: buildLectureThreeSumSnapshot,
      },
      {
        id: "lecture-3-extrema-update",
        title: "最大值 / 最小值更新动画",
        description: "逐格比较当前元素与 mx / mn，明确什么时候触发更新、什么时候保持不变。",
        maxStep: 9,
        getSnapshot: buildLectureThreeExtremaSnapshot,
      },
      {
        id: "lecture-3-conditional-count",
        title: "条件计数动画",
        description: "用“统计偶数个数”演示 cnt 在条件成立时如何增加。",
        maxStep: 9,
        getSnapshot: buildLectureThreeConditionalCountSnapshot,
      },
      {
        id: "lecture-3-fixed-window",
        title: "固定窗口动画",
        description: "同一模块内完整演示 2×2 和 3×3 两种固定窗口的合法左上角移动与局部统计。",
        maxStep: 13,
        getSnapshot: buildLectureThreeFixedWindowSnapshot,
      },
    ],
  },
  "lecture-4-template-animation": {
    suiteId: "lecture-4-suite",
    intro:
      "第 4 讲开始把二维数组用作字符地图。字符读入、上下左右和画布裁剪三组动画继续沿用同一套 step controller、Debug 区和代码高亮。",
    modules: [
      {
        id: "lecture-4-char-input",
        title: "字符网格读入动画",
        description: "把每一行字符串逐字符写入字符网格，建立“字符串行 -> 二维字符矩阵”的映射。",
        maxStep: 12,
        getSnapshot: buildLectureFourCharInputSnapshot,
      },
      {
        id: "lecture-4-neighbors",
        title: "上下左右动画",
        description: "以中心格为起点，依次访问上、下、左、右四个邻居，强调坐标变化和表达式写法。",
        maxStep: 5,
        getSnapshot: buildLectureFourNeighborSnapshot,
      },
      {
        id: "lecture-4-crop",
        title: "画布裁剪动画",
        description: "在完整字符画布上高亮裁剪区域，并逐步把裁剪结果复制到右侧新区域。",
        maxStep: 6,
        getSnapshot: buildLectureFourCropSnapshot,
      },
    ],
  },
  "lecture-5-template-animation": {
    suiteId: "lecture-5-suite",
    intro:
      "第 5 讲继续在字符网格上做局部观察。八方向、子矩形左上角枚举和 check 函数局部判断都复用现有播放器，并继续保留 Debug 区和代码执行行高亮。",
    modules: [
      {
        id: "lecture-5-eight-directions",
        title: "八方向动画",
        description: "围绕中心格依次访问左上、上、右上、左、右、左下、下、右下八个方向，建立完整邻域意识。",
        maxStep: 9,
        getSnapshot: buildLectureFiveEightDirectionSnapshot,
      },
      {
        id: "lecture-5-subrect-left-top",
        title: "子矩形左上角枚举动画",
        description: "在较大网格中枚举固定大小子矩形的合法左上角，并解释为什么当前位置仍然合法。",
        maxStep: 9,
        getSnapshot: buildLectureFiveSubrectSnapshot,
      },
      {
        id: "lecture-5-check-function",
        title: "check 函数局部判断动画",
        description: "从某个左上角出发逐格检查图案，明确展示“继续检查 / 通过 / 失败”以及失败发生的位置。",
        maxStep: 4,
        getSnapshot: buildLectureFiveCheckSnapshot,
      },
    ],
  },
  "lecture-6-template-animation": {
    suiteId: "lecture-6-suite",
    intro:
      "第 6 讲作为收口模块，把翻转、转置、行列交换和一维展开映射全部串起来。六组动画继续复用同一套播放器、按钮、Debug 区和代码高亮。",
    modules: [
      {
        id: "lecture-6-horizontal-flip",
        title: "左右翻转动画",
        description: "按行逐对交换左右位置，展示左右翻转不是一下子变结果，而是一个个 swap 完成。",
        maxStep: 6,
        getSnapshot: buildLectureSixHorizontalFlipSnapshot,
      },
      {
        id: "lecture-6-vertical-flip",
        title: "上下翻转动画",
        description: "逐列交换上行和下行，展示上下翻转时每对行如何一步步完成互换。",
        maxStep: 6,
        getSnapshot: buildLectureSixVerticalFlipSnapshot,
      },
      {
        id: "lecture-6-transpose",
        title: "转置动画",
        description: "同时显示原矩阵和目标矩阵，逐步把 a[i][j] 放到 b[j][i]。",
        maxStep: 9,
        getSnapshot: buildLectureSixTransposeSnapshot,
      },
      {
        id: "lecture-6-row-swap",
        title: "行交换动画",
        description: "选中两整行后按列逐格交换，强调整行交换其实由多个单元交换组成。",
        maxStep: 4,
        getSnapshot: buildLectureSixRowSwapSnapshot,
      },
      {
        id: "lecture-6-col-swap",
        title: "列交换动画",
        description: "选中两列后按行逐格交换，明确“列交换”是顺着每一行依次完成的。",
        maxStep: 4,
        getSnapshot: buildLectureSixColSwapSnapshot,
      },
      {
        id: "lecture-6-linear-map",
        title: "矩阵与一维展开映射动画",
        description: "同时显示二维矩阵和一维线性数组，逐步解释为什么二维数组是按行展开的。",
        maxStep: 12,
        getSnapshot: buildLectureSixLinearMapSnapshot,
      },
    ],
  },
};

bootstrap().catch((error) => {
  console.error(error);
  document.getElementById("page-title").textContent = "页面加载失败";
  document.getElementById("page-subtitle").textContent = String(error);
});

async function bootstrap() {
  const data = await loadSiteData();
  const questionsById = new Map(data.questions.map((question) => [question.id, question]));
  const pageMode = topicAppRoot?.dataset.page || "home";

  if (pageMode === "lecture") {
    renderLecturePage(data, questionsById);
  } else {
    renderHomePage(data);
  }

  setupToggles();
  setupAnimationHotkeys();
  if (pageMode === "lecture") {
    setupLectureTeachingUI();
    setupNavHighlight();
    scrollToHashTarget();
  }
}

async function loadSiteData() {
  const embeddedData = document.getElementById("gesp4-array-2d-site-data");
  if (embeddedData?.textContent) {
    return JSON.parse(embeddedData.textContent);
  }

  throw new Error("未找到二维数组专题数据。");
}

function renderHomePage(data) {
  document.title = data.meta.title;
  renderHomeMeta(data);
  renderHomeOutline(data.lectures);
  renderLectureEntryGrid(data.lectures);
}

function renderHomeMeta(data) {
  const successCount = data.questions.filter((question) => question.contentStatus === "success").length;
  const paperCount = data.extractionReport.length;
  const totalModules = countTotalModules(data.lectures);
  const questionTypeSummary = [...new Set(data.questions.map((question) => typeLabels[question.type] || question.type))].join("、");

  document.getElementById("page-title").textContent = data.meta.title;
  document.getElementById("page-subtitle").textContent = data.meta.subtitle;
  document.getElementById("sidebar-summary").textContent =
    `共保留 ${data.lectures.length} 讲主线、${data.questions.length} 道真题、${totalModules} 个动画模块与 ${paperCount} 份试卷来源，首页只承担专题导读与目录功能。`;

  const metaGrid = document.getElementById("meta-grid");
  metaGrid.replaceChildren(
    createMetaCard(data.lectures.length, "专题讲次"),
    createMetaCard(data.questions.length, "精选真题"),
    createMetaCard(totalModules, "动画模块"),
    createMetaCard(paperCount, "覆盖试卷"),
  );

  const techStackNotes = document.getElementById("tech-stack-notes");
  techStackNotes.replaceChildren(
    createParagraph("专题内容已经从旧项目的独立专题站壳子里抽出，首页现在只保留导读、目录和内容分发，适合挂到学生端专题详情层中。"),
    createBulletList([
      "先建立二维数组的合法定义、坐标定位和双重循环遍历。",
      "再把按行理解、整行偏移和函数传参连成统一认知。",
      "最后进入统计、字符网格和矩阵变换等应用题型。",
    ]),
  );

  const sourceDocuments = document.getElementById("source-documents");
  sourceDocuments.replaceChildren(
    createOverviewStack([
      {
        title: "第1-2讲",
        text: "把定义、下标、遍历、按行存储和函数传参打稳，解决“二维数组到底是什么”的理解问题。",
      },
      {
        title: "第3讲",
        text: "进入求和、最值、计数和固定窗口，训练整表统计与变量管理。",
      },
      {
        title: "第4-5讲",
        text: "把二维数组迁移到字符网格、方向访问、子矩形和 check 判断场景。",
      },
      {
        title: "第6讲",
        text: "用翻转、转置、交换和展开映射收束整个专题的矩阵操作能力。",
      },
    ]),
  );

  const extractionSummary = document.getElementById("extraction-summary");
  extractionSummary.replaceChildren(
    createParagraph(`当前专题已整理 ${successCount} 道稳定内容，题型覆盖 ${questionTypeSummary}。`),
    createBulletList([
      "每讲继续保留“本讲目标 -> 核心知识点 -> 模板/动画 -> 真题训练”的主体结构。",
      "讲次页不再依赖独立站式左侧导航，改成可嵌入系统内容层的轻量目录。",
      "教师演示能力继续保留，但默认从学生视角弱化，不再占据主视觉。",
    ]),
  );

  const reportGrid = document.getElementById("report-grid");
  reportGrid.replaceChildren();
  buildPaperCoverageRows(data.questions).forEach((row) => {
    const card = document.createElement("article");
    card.className = "report-card";

    const title = document.createElement("h4");
    title.textContent = row.paperLabel;

    const badge = createStatusTag("success", `${row.questionCount} 道题`);

    const extracted = document.createElement("strong");
    extracted.textContent = String(row.lectureTitles.length);

    const label = document.createElement("span");
    label.textContent = `涉及 ${row.lectureTitles.join("、")}`;

    const typeSummary = document.createElement("span");
    typeSummary.className = "source-path";
    typeSummary.textContent = `题型：${row.typeLabels.join("、")}`;

    card.append(title, badge, extracted, label, typeSummary);
    reportGrid.append(card);
  });
}

function renderHomeOutline(lectures) {
  const navRoot = document.getElementById("sidebar-nav");
  if (!navRoot) {
    return;
  }

  const lectureRow = document.createElement("div");
  lectureRow.className = "outline-chip-row";
  lectures.forEach((lecture) => {
    lectureRow.append(createOutlineChipLink(lecture.title, createLectureHref(lecture.id), getLectureChipText(lecture.id)));
  });

  const stageGrid = document.createElement("div");
  stageGrid.className = "outline-note-grid";
  getTopicStageNotes(lectures).forEach((stage) => {
    stageGrid.append(createOutlineNoteCard(stage.title, stage.description));
  });

  navRoot.replaceChildren(
    createOutlineGroup("按讲次进入", "目录只保留专题内部跳转和讲次入口，不再承担站级导航。", lectureRow),
    createOutlineGroup("学习主线", "按知识递进组织，方便后续直接挂进系统内容层。", stageGrid),
  );
}

function renderLectureEntryGrid(lectures) {
  const grid = document.getElementById("lecture-entry-grid");
  if (!grid) {
    return;
  }

  grid.replaceChildren(...lectures.map((lecture) => createLectureEntryCard(lecture)));
}

function createLectureEntryCard(lecture) {
  const card = document.createElement("article");
  card.className = "lecture-entry-card";

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow entry-eyebrow";
  eyebrow.textContent = `Lecture ${lecture.id.replace("lecture-", "")}`;

  const title = document.createElement("h4");
  title.textContent = lecture.title;

  const summary = document.createElement("p");
  summary.className = "lecture-entry-summary";
  summary.textContent = lecture.summary;

  const meta = document.createElement("div");
  meta.className = "lecture-entry-meta";
  meta.append(
    createInlineMeta(`${countLectureQuestions(lecture)} 道题`),
    createInlineMeta(`${countLectureModules(lecture)} 个动画模块`),
    createInlineMeta(getLectureChipText(lecture.id)),
  );

  const link = document.createElement("a");
  link.className = "page-link";
  link.href = createLectureHref(lecture.id);
  link.textContent = "进入本讲";

  card.append(eyebrow, title, summary, meta, link);
  return card;
}

function createInlineMeta(text) {
  const meta = document.createElement("span");
  meta.className = "question-badge";
  meta.textContent = text;
  return meta;
}

function renderLecturePage(data, questionsById) {
  const lecture = getCurrentLecture(data.lectures);
  document.title = `${lecture.title} | ${data.meta.title}`;
  teacherState.currentLectureId = lecture.id;

  renderLectureHero(data, lecture);
  renderLectureOutline(data.lectures, lecture);
  renderLectureContent(lecture, questionsById);
}

function getCurrentLecture(lectures) {
  const lectureId = new URLSearchParams(window.location.search).get("lecture");
  return lectures.find((lecture) => lecture.id === lectureId) || lectures[0];
}

function renderLectureHero(data, lecture) {
  document.getElementById("sidebar-summary").textContent =
    `本讲保留 ${lecture.sections.length} 个一级模块、${countLectureQuestions(lecture)} 道真题与 ${countLectureModules(lecture)} 个动画模块，适合作为专题内容层内的一页讲次内容。`;
  document.getElementById("page-title").textContent = lecture.title;
  document.getElementById("page-subtitle").textContent = lecture.summary;

  const metaGrid = document.getElementById("meta-grid");
  metaGrid.replaceChildren(
    createMetaCard(countLectureQuestions(lecture), "本讲真题"),
    createMetaCard(countLectureModules(lecture), "动画模块"),
    createMetaCard(lecture.sections.length, "一级模块"),
    createMetaCard(collectCoveredPapers(lecture).length, "覆盖试卷"),
  );

  const heroActions = document.getElementById("hero-actions");
  if (!heroActions) {
    return;
  }

  const lectureIndex = data.lectures.findIndex((item) => item.id === lecture.id);
  const previousLecture = data.lectures[lectureIndex - 1];
  const nextLecture = data.lectures[lectureIndex + 1];

  heroActions.replaceChildren(
    createActionLink("返回专题首页", createTopicHomeHref()),
    ...(previousLecture ? [createActionLink("上一讲", createLectureHref(previousLecture.id))] : []),
    ...(nextLecture ? [createActionLink("下一讲", createLectureHref(nextLecture.id))] : []),
    createActionButton("进入教师模式", "toggle-teacher-mode"),
  );
}

function createActionLink(label, href) {
  const link = document.createElement("a");
  link.className = "page-link subtle";
  link.href = href;
  link.textContent = label;
  return link;
}

function createActionButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "page-link subtle teacher-mode-button";
  button.dataset.teacherAction = action;
  button.textContent = label;
  return button;
}

function createTeacherToolbar(lecture) {
  const toolbar = document.createElement("section");
  toolbar.className = "teacher-toolbar";

  const head = document.createElement("div");
  head.className = "teacher-toolbar-head";

  const titleGroup = document.createElement("div");
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow teacher-eyebrow";
  eyebrow.textContent = "Teacher Mode";
  const title = document.createElement("h3");
  title.textContent = "教师演示控制台";
  const description = document.createElement("p");
  description.className = "teacher-toolbar-description";
  description.textContent = "统一控制思路、答案、代码和动画聚焦，讲课时尽量少切页面、少滚大段内容。";
  titleGroup.append(eyebrow, title, description);

  const actionRow = document.createElement("div");
  actionRow.className = "teacher-toolbar-actions";
  actionRow.append(
    createTeacherControlButton("聚焦当前动画", "toggle-focus-current-module"),
    createTeacherControlButton("显示键盘提示", "toggle-keyboard-hint"),
  );

  head.append(titleGroup, actionRow);
  toolbar.append(head);

  const statusRow = document.createElement("div");
  statusRow.className = "teacher-status-row";
  statusRow.append(
    createTeacherStatusPill("当前讲次", lecture.title, "lecture"),
    createTeacherStatusPill("当前模块", "未激活动画模块", "module"),
    createTeacherStatusPill("当前题目", "未选中题目", "question"),
  );
  toolbar.append(statusRow);

  const quickNav = document.createElement("div");
  quickNav.className = "lecture-quick-nav";
  buildLectureQuickNav(lecture).forEach((item) => quickNav.append(item));
  toolbar.append(quickNav);

  const controlGrid = document.createElement("div");
  controlGrid.className = "teacher-control-grid";
  controlGrid.append(
    createTeacherControlGroup("思路", [
      createTeacherControlButton("折叠全部思路", "collapse-all-panels", "analysis"),
      createTeacherControlButton("展开当前思路", "expand-current-panel", "analysis"),
    ]),
    createTeacherControlGroup("答案", [
      createTeacherControlButton("折叠全部答案", "collapse-all-panels", "answer"),
      createTeacherControlButton("展开当前答案", "expand-current-panel", "answer"),
    ]),
    createTeacherControlGroup("代码", [
      createTeacherControlButton("折叠全部代码", "collapse-all-panels", "code"),
      createTeacherControlButton("展开当前代码", "expand-current-panel", "code"),
    ]),
  );
  toolbar.append(controlGrid);

  return toolbar;
}

function createTeacherStatusPill(label, value, key) {
  const pill = document.createElement("div");
  pill.className = "teacher-status-pill";
  pill.dataset.teacherStatus = key;
  pill.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
  return pill;
}

function createTeacherControlGroup(title, buttons) {
  const group = document.createElement("section");
  group.className = "teacher-control-group";

  const heading = document.createElement("h4");
  heading.textContent = title;
  const row = document.createElement("div");
  row.className = "teacher-button-row";
  row.append(...buttons);

  group.append(heading, row);
  return group;
}

function createTeacherControlButton(label, action, panelType = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "teacher-control-button";
  button.dataset.teacherAction = action;
  if (panelType) {
    button.dataset.panelType = panelType;
  }
  button.textContent = label;
  return button;
}

function getLectureQuickLinks(lecture) {
  const coreSection = lecture.sections.find((section) => section.id.includes("-core"));
  const coreChildren = coreSection?.children || [];
  const knowledgeChild = coreChildren.find((child) => Array.isArray(child.questionIds) && child.questionIds.length) || coreChildren[0];
  const mistakesChild = coreChildren.find((child) => child.id.includes("mistakes"));
  const templateChild = coreChildren.find((child) => Array.isArray(child.resources) && child.resources.length);
  const animationChild =
    lecture.sections.flatMap((section) => section.children || []).find((child) => animationSuitesByChildId[child.id]) || null;

  const links = [
    ["知识点说明", knowledgeChild?.id || coreSection?.id],
    ["易错点", mistakesChild?.id],
    ["模板", templateChild?.id],
    ["动画", animationChild?.id],
    ["真题", knowledgeChild?.id],
  ];

  return links
    .filter(([, targetId]) => Boolean(targetId))
    .map(([label, targetId]) => ({
      label,
      targetId,
      href: createLectureHref(lecture.id, targetId),
    }));
}

function buildLectureQuickNav(lecture) {
  return getLectureQuickLinks(lecture).map((item) => {
    const link = document.createElement("a");
    link.className = "lecture-quick-chip";
    link.href = item.href;
    link.textContent = item.label;
    return link;
  });
}

function renderLectureOutline(lectures, currentLecture) {
  const navRoot = document.getElementById("sidebar-nav");
  if (!navRoot) {
    return;
  }

  const lectureRow = document.createElement("div");
  lectureRow.className = "outline-chip-row";
  lectures.forEach((lecture) => {
    lectureRow.append(
      createOutlineChipLink(lecture.title, createLectureHref(lecture.id), getLectureChipText(lecture.id), lecture.id === currentLecture.id),
    );
  });

  const quickRow = document.createElement("div");
  quickRow.className = "outline-chip-row";
  getLectureQuickLinks(currentLecture).forEach((item) => {
    quickRow.append(createOutlineChipLink(item.label, item.href));
  });

  const sectionList = document.createElement("div");
  sectionList.className = "outline-anchor-list";
  currentLecture.sections.forEach((section) => {
    const group = document.createElement("section");
    group.className = "section-link-group";

    const head = document.createElement("div");
    head.className = "section-link-head";
    head.append(
      createNavLink({
        text: section.title,
        level: 1,
        href: createLectureHref(currentLecture.id, section.id),
        targetId: section.id,
      }),
    );

    const note = document.createElement("span");
    note.className = "outline-group-note";
    note.textContent = section.children?.length ? `${section.children.length} 个子主题` : `${section.points?.length || 0} 条要点`;
    head.append(note);
    group.append(head);

    if (section.children?.length) {
      const childRow = document.createElement("div");
      childRow.className = "section-link-children";
      section.children.forEach((child) => {
        childRow.append(
          createNavLink({
            text: child.title,
            level: 2,
            href: createLectureHref(currentLecture.id, child.id),
            targetId: child.id,
          }),
        );
      });
      group.append(childRow);
    }

    sectionList.append(group);
  });

  const groups = [
    createOutlineGroup("讲次切换", `当前：${currentLecture.title}`, lectureRow),
    createOutlineGroup("详细目录", `${currentLecture.sections.length} 个一级模块`, sectionList),
  ];

  if (quickRow.childElementCount) {
    groups.splice(1, 0, createOutlineGroup("本讲速查", "快速跳到知识点、模板、动画和真题区域。", quickRow));
  }

  navRoot.replaceChildren(...groups);
}

function createMetaCard(value, label) {
  const card = document.createElement("article");
  card.className = "meta-card";

  const number = document.createElement("strong");
  number.textContent = String(value);

  const text = document.createElement("span");
  text.textContent = label;

  card.append(number, text);
  return card;
}

function createNavLink({ href, text, level, targetId = "", active = false }) {
  const link = document.createElement("a");
  link.className = `outline-anchor level-${level}`;
  link.href = href;

  if (targetId) {
    link.dataset.target = targetId;
  }
  if (active) {
    link.classList.add("is-page-active");
  }

  const span = document.createElement("span");
  span.textContent = text;
  link.append(span);
  return link;
}

function renderLectureContent(lecture, questionsById) {
  const container = document.getElementById("lecture-container");
  const lectureSection = document.createElement("section");
  lectureSection.className = "lecture-section observe-target";
  lectureSection.id = lecture.id;

  const head = document.createElement("div");
  head.className = "lecture-head";

  const titleWrapper = document.createElement("div");
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Lecture";
  const title = document.createElement("h2");
  title.textContent = lecture.title;
  const summary = document.createElement("p");
  summary.textContent = lecture.summary;
  titleWrapper.append(eyebrow, title, summary);

  const chip = document.createElement("div");
  chip.className = "lecture-chip";
  chip.textContent = getLectureChipText(lecture.id);

  head.append(titleWrapper, chip);
  lectureSection.append(head);

  lecture.sections.forEach((section) => {
    const sectionBlock = document.createElement("section");
    sectionBlock.className = "section-block observe-target";
    sectionBlock.id = section.id;

    const sectionTitle = document.createElement("h3");
    sectionTitle.textContent = section.title;
    sectionBlock.append(sectionTitle);

    if (section.points?.length) {
      sectionBlock.append(createBulletList(section.points));
    }

    if (section.children?.length) {
      const topicGrid = document.createElement("div");
      topicGrid.className = "topic-grid";

      section.children.forEach((child) => {
        const topicCard = document.createElement("article");
        topicCard.className = "topic-card observe-target";
        topicCard.id = child.id;

        const topicHeader = document.createElement("div");
        topicHeader.className = "topic-header";
        const childTitle = document.createElement("h3");
        childTitle.textContent = child.title;
        topicHeader.append(childTitle);

        if (Array.isArray(child.questionIds) && child.questionIds.length) {
          topicHeader.append(createStatusTag("success", `${child.questionIds.length} 道题`));
        } else if (animationSuitesByChildId[child.id]) {
          topicHeader.append(createStatusTag("success", `${animationSuitesByChildId[child.id].modules.length} 个模块`));
        }

        topicCard.append(topicHeader);

        if (child.points?.length) {
          const topicBody = document.createElement("div");
          topicBody.className = "topic-body";
          topicBody.append(createBulletList(child.points));
          topicCard.append(topicBody);
        }

        if (Array.isArray(child.resources) && child.resources.length) {
          topicCard.append(createTopicResources(child.resources));
        }

        if (animationSuitesByChildId[child.id]) {
          topicCard.append(createAnimationSuite(animationSuitesByChildId[child.id]));
        }

        if (Array.isArray(child.questionIds) && child.questionIds.length) {
          const questionList = document.createElement("div");
          questionList.className = "question-list";
          child.questionIds
            .map((questionId) => questionsById.get(questionId))
            .filter(Boolean)
            .forEach((question) => questionList.append(createQuestionCard(question)));
          topicCard.append(questionList);
        }

        topicGrid.append(topicCard);
      });

      sectionBlock.append(topicGrid);
    }

    lectureSection.append(sectionBlock);
  });

  container.replaceChildren(createTeacherToolbar(lecture), lectureSection);
}

function createLectureHref(lectureId, anchorId = "") {
  const url = new URL(window.location.href);
  url.searchParams.set("lecture", lectureId);
  url.hash = anchorId ? `#${anchorId}` : "";
  return `${url.pathname}${url.search}${url.hash}`;
}

function createTopicHomeHref() {
  return window.location.pathname;
}

function countLectureQuestions(lecture) {
  const questionIds = new Set();
  lecture.sections.forEach((section) => {
    section.children?.forEach((child) => {
      child.questionIds?.forEach((questionId) => questionIds.add(questionId));
    });
  });
  return questionIds.size;
}

function countTotalModules(lectures) {
  return lectures.reduce((sum, lecture) => sum + countLectureModules(lecture), 0);
}

function countLectureModules(lecture) {
  return lecture.sections.reduce((sum, section) => {
    return (
      sum +
      (section.children || []).reduce((childSum, child) => {
        return childSum + (animationSuitesByChildId[child.id]?.modules.length || 0);
      }, 0)
    );
  }, 0);
}

function collectCoveredPapers(lecture) {
  const paperKeys = new Set();
  lecture.sections.forEach((section) => {
    section.children?.forEach((child) => {
      child.questionIds?.forEach((questionId) => {
        const matched = questionId.match(/^q-(\d{4}-\d{2})-/);
        if (matched) {
          paperKeys.add(matched[1]);
        }
      });
    });
  });
  return [...paperKeys];
}

function buildPaperCoverageRows(questions) {
  const rows = new Map();

  questions.forEach((question) => {
    if (!rows.has(question.paperKey)) {
      rows.set(question.paperKey, {
        paperKey: question.paperKey,
        paperLabel: question.paperLabel,
        lectureTitles: new Set(),
        typeLabels: new Set(),
        questionCount: 0,
      });
    }

    const row = rows.get(question.paperKey);
    row.questionCount += 1;
    row.lectureTitles.add(question.lectureId.replace("lecture-", "第") + "讲");
    row.typeLabels.add(typeLabels[question.type] || question.type);
  });

  return [...rows.values()]
    .sort((a, b) => b.paperKey.localeCompare(a.paperKey))
    .map((row) => ({
      paperLabel: row.paperLabel,
      questionCount: row.questionCount,
      lectureTitles: [...row.lectureTitles],
      typeLabels: [...row.typeLabels],
    }));
}

function getLectureChipText(lectureId) {
  if (lectureId === "lecture-1") {
    return "第1讲已精修";
  }
  if (lectureId === "lecture-2") {
    return "第2讲第一版";
  }
  if (lectureId === "lecture-3") {
    return "第3讲第一版";
  }
  if (lectureId === "lecture-4") {
    return "第4讲第一版";
  }
  if (lectureId === "lecture-5") {
    return "第5讲第一版";
  }
  if (lectureId === "lecture-6") {
    return "第6讲第一版";
  }
  return "动画区已预留";
}

function getTopicStageNotes(lectures) {
  return [
    {
      title: "基础定位",
      description: `${lectures[0]?.title || "第1讲"} 与 ${lectures[1]?.title || "第2讲"} 负责把定义、下标、按行理解和传参打稳。`,
    },
    {
      title: "统计处理",
      description: `${lectures[2]?.title || "第3讲"} 把求和、最值、计数和固定窗口串成一条处理主线。`,
    },
    {
      title: "字符网格",
      description: `${lectures[3]?.title || "第4讲"} 与 ${lectures[4]?.title || "第5讲"} 负责方向、裁剪、子矩形和 check。`,
    },
    {
      title: "矩阵变换",
      description: `${lectures[5]?.title || "第6讲"} 用翻转、转置和展开映射把整个专题收口。`,
    },
  ];
}

function createQuestionCard(question) {
  const fragment = questionTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".question-card");
  const meta = fragment.querySelector(".question-meta");
  const status = fragment.querySelector(".question-status");
  const title = fragment.querySelector(".question-title");
  const summary = fragment.querySelector(".question-summary");
  const questionText = fragment.querySelector(".question-text");
  const notes = fragment.querySelector(".question-notes");
  const classificationReason = fragment.querySelector(".classification-reason");
  const pitfall = fragment.querySelector(".pitfall");
  const actions = fragment.querySelector(".question-actions");
  const panels = fragment.querySelector(".question-panels");
  card.dataset.questionId = question.id;

  meta.append(
    createQuestionBadge(question.paperLabel),
    createQuestionBadge(`${typeLabels[question.type] || question.type} · 第${question.questionNumber}题`),
  );

  status.append(createStatusTag(question.contentStatus === "success" ? "success" : "partial", statusLabels[question.contentStatus]));
  title.textContent = question.title;
  summary.textContent = question.summary;
  questionText.replaceChildren(renderQuestionTextContent(question.questionText));
  classificationReason.textContent = question.classificationReason;
  pitfall.closest("div")?.remove();
  notes.classList.add("single-column");

  const answerPanelId = `${question.id}-answer`;
  if (question.analysisText || question.studyGuide) {
    const analysisPanelId = `${question.id}-analysis`;
    actions.append(createToggleButton(question.type === "programming" ? "显示思路" : "显示简析", analysisPanelId, "analysis"));
    panels.append(createAnalysisPanel(analysisPanelId, question));
  }

  actions.append(createToggleButton("显示答案", answerPanelId, "answer"));
  panels.append(createAnswerPanel(answerPanelId, question.answerText, question.pitfall));

  if (question.referenceCode) {
    const codePanelId = `${question.id}-code`;
    actions.append(createToggleButton("显示代码", codePanelId, "code"));
    panels.append(createCodePanel(codePanelId, "参考代码", question.referenceCode));
  }

  if (question.contentStatus !== "success") {
    const note = document.createElement("div");
    note.className = "question-panel";
    note.innerHTML = "<h5>抽取备注</h5><p>该题当前主要依赖摘要或模板兜底，后续仍可继续清洗原卷噪声。</p>";
    panels.append(note);
  }

  return card;
}

function createQuestionBadge(text) {
  const badge = document.createElement("span");
  badge.className = "question-badge";
  badge.textContent = text;
  return badge;
}

function createStatusTag(kind, text) {
  const badge = document.createElement("span");
  badge.className = `status-tag ${kind}`;
  badge.textContent = text;
  return badge;
}

function createToggleButton(label, panelId, panelType = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "toggle-button";
  button.dataset.target = panelId;
  button.dataset.labelShow = label;
  button.dataset.labelHide = label.replace("显示", "隐藏");
  if (panelType) {
    button.dataset.panelType = panelType;
  }
  button.textContent = label;
  return button;
}

function createTextPanel(id, title, text) {
  const panel = document.createElement("section");
  panel.className = "question-panel is-hidden";
  panel.id = id;

  const heading = document.createElement("h5");
  heading.textContent = title;

  const content = document.createElement("div");
  content.replaceChildren(renderQuestionTextContent(text));

  panel.append(heading, content);
  return panel;
}

function createAnalysisPanel(id, question) {
  const panel = document.createElement("section");
  panel.className = "question-panel is-hidden";
  panel.id = id;
  panel.dataset.panelType = "analysis";

  const heading = document.createElement("h5");
  heading.textContent = question.type === "programming" ? "编程题思路拆解" : "客观题简析";

  const stack = document.createElement("div");
  stack.className = "answer-panel-stack";

  if (question.analysisText) {
    stack.append(createPanelContentBlock(question.type === "programming" ? "题目切入口" : "简析", question.analysisText));
  }

  if (question.studyGuide) {
    const guideFields = [
      ["怎么读题", question.studyGuide.reading],
      ["为什么想到二维数组", question.studyGuide.why2d],
      ["双重循环怎么划分", question.studyGuide.loops],
      ["关键点", question.studyGuide.key],
      ["最容易错在哪里", question.studyGuide.pitfall],
    ];

    guideFields.forEach(([label, value]) => {
      if (!value) {
        return;
      }
      stack.append(createPanelContentBlock(label, value));
    });
  }

  panel.append(heading, stack);
  return panel;
}

function createAnswerPanel(id, answerText, pitfallText) {
  const panel = document.createElement("section");
  panel.className = "question-panel is-hidden";
  panel.id = id;
  panel.dataset.panelType = "answer";

  const heading = document.createElement("h5");
  heading.textContent = "参考答案";

  const stack = document.createElement("div");
  stack.className = "answer-panel-stack";

  const answerBlock = document.createElement("div");
  answerBlock.className = "answer-panel-block";

  answerBlock.append(createPanelContentHeading("答案"));
  const answerContent = document.createElement("div");
  answerContent.replaceChildren(renderQuestionTextContent(answerText));
  answerBlock.append(answerContent);
  stack.append(answerBlock);

  if (pitfallText) {
    const pitfallBlock = document.createElement("div");
    pitfallBlock.className = "answer-panel-block";

    const pitfallTitle = createPanelContentHeading("易错点");
    const pitfallContent = document.createElement("p");
    pitfallContent.textContent = pitfallText;

    pitfallBlock.append(pitfallTitle, pitfallContent);
    stack.append(pitfallBlock);
  }

  panel.append(heading, stack);
  return panel;
}

function createPanelContentBlock(title, text) {
  const block = document.createElement("div");
  block.className = "answer-panel-block";
  block.append(createPanelContentHeading(title));

  const content = document.createElement("div");
  content.replaceChildren(renderQuestionTextContent(text));
  block.append(content);
  return block;
}

function createPanelContentHeading(title) {
  const heading = document.createElement("h6");
  heading.textContent = title;
  return heading;
}

function createCodePanel(id, title, code) {
  const panel = document.createElement("section");
  panel.className = "question-panel is-hidden";
  panel.id = id;
  panel.dataset.panelType = "code";

  const heading = document.createElement("h5");
  heading.textContent = title;

  const pre = document.createElement("pre");
  const codeNode = document.createElement("code");
  codeNode.textContent = formatDisplayCode(code);
  pre.append(codeNode);

  panel.append(heading, pre);
  return panel;
}

function createBulletList(items) {
  const list = document.createElement("ul");
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  });
  return list;
}

function createTopicResources(resources) {
  const stack = document.createElement("div");
  stack.className = "topic-resource-stack";

  resources.forEach((resource) => {
    const card = document.createElement("section");
    card.className = "topic-resource";

    const title = document.createElement("h4");
    title.textContent = resource.title;
    card.append(title);

    if (resource.description) {
      const description = document.createElement("div");
      description.className = "topic-resource-description";
      description.replaceChildren(renderQuestionTextContent(resource.description));
      card.append(description);
    }

    if (resource.points?.length) {
      card.append(createBulletList(resource.points));
    }

    if (resource.code) {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = formatDisplayCode(resource.code);
      pre.append(code);
      card.append(pre);
    }

    stack.append(card);
  });

  return stack;
}

function createParagraph(text) {
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  return paragraph;
}

function createOverviewStack(items) {
  const stack = document.createElement("div");
  stack.className = "overview-stack";
  items.forEach((item) => {
    stack.append(createOverviewLine(item.title, item.text));
  });
  return stack;
}

function createOverviewLine(title, text) {
  const item = document.createElement("article");
  item.className = "overview-line";

  const heading = document.createElement("strong");
  heading.textContent = title;
  const body = document.createElement("p");
  body.textContent = text;

  item.append(heading, body);
  return item;
}

function createOutlineGroup(title, note, body) {
  const group = document.createElement("section");
  group.className = "outline-group";

  const head = document.createElement("div");
  head.className = "outline-group-head";

  const titleNode = document.createElement("strong");
  titleNode.className = "outline-group-title";
  titleNode.textContent = title;
  head.append(titleNode);

  if (note) {
    const noteNode = document.createElement("span");
    noteNode.className = "outline-group-note";
    noteNode.textContent = note;
    head.append(noteNode);
  }

  group.append(head, body);
  return group;
}

function createOutlineChipLink(label, href, meta = "", active = false) {
  const link = document.createElement("a");
  link.className = "outline-chip";
  link.href = href;
  if (active) {
    link.classList.add("is-active");
  }

  const labelNode = document.createElement("span");
  labelNode.textContent = label;
  link.append(labelNode);

  if (meta) {
    const metaNode = document.createElement("small");
    metaNode.textContent = meta;
    link.append(metaNode);
  }

  return link;
}

function createOutlineNoteCard(title, text) {
  const card = document.createElement("article");
  card.className = "outline-note-card";

  const heading = document.createElement("strong");
  heading.textContent = title;

  const body = document.createElement("p");
  body.textContent = text;

  card.append(heading, body);
  return card;
}

function renderQuestionTextContent(text) {
  const wrapper = document.createElement("div");
  wrapper.className = "question-text-content";

  const lines = String(text || "")
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.replace(/\u00a0/g, " ").trimEnd());

  const paragraphLines = [];
  const codeLines = [];

  const flushParagraph = () => {
    if (!paragraphLines.length) {
      return;
    }
    const paragraph = document.createElement("p");
    paragraph.textContent = paragraphLines.join("\n");
    wrapper.append(paragraph);
    paragraphLines.length = 0;
  };

  const flushCode = () => {
    if (!codeLines.length) {
      return;
    }
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = formatDisplayCode(codeLines.join("\n"));
    pre.append(code);
    wrapper.append(pre);
    codeLines.length = 0;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushCode();
      return;
    }
    if (/^\d+$/.test(trimmed)) {
      return;
    }
    if (isCodeLikeLine(trimmed)) {
      flushParagraph();
      codeLines.push(trimmed);
      return;
    }
    flushCode();
    paragraphLines.push(trimmed);
  });

  flushParagraph();
  flushCode();

  if (!wrapper.childNodes.length) {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    wrapper.append(paragraph);
  }

  return wrapper;
}

function isCodeLikeLine(line) {
  return (
    /^(#include|using namespace|const |int |long long|char |bool |double |float |string |vector<|struct |void |return\b)/.test(
      line,
    ) ||
    /^(for|while|if|else|scanf|printf|cin|cout|std::)\b/.test(line) ||
    /^(?:\{.*|[\{\}];?)$/.test(line) ||
    /^[A-Za-z_]\w*\s*\[.*\]\s*=/.test(line)
  );
}

function formatDisplayCode(code) {
  const merged = String(code || "")
    .replace(/\r/g, "")
    .replace(/\n\s*_\s*\n/g, "_")
    .replace(/\n\s*,/g, ",")
    .replace(/'\s*\n\s*([.#01])\s*\n\s*'/g, "'$1'")
    .replace(/"\s*\n\s*,/g, '",')
    .replace(/\n\s*\)\s*;/g, ");");

  const lines = merged
    .split("\n")
    .map((line) => line.replace(/\t/g, "    ").trimEnd())
    .filter((line, index, source) => {
      if (/^[\]）】]+$/.test(line.trim())) {
        return false;
      }
      if (/^\d+\.$/.test(line.trim())) {
        return false;
      }
      return !(line === "" && source[index - 1] === "");
    });

  return lines.join("\n").trim();
}

function createAnimationSuite(suiteConfig) {
  const wrapper = document.createElement("div");
  wrapper.className = "animation-suite";

  const intro = document.createElement("div");
  intro.className = "animation-intro";
  intro.innerHTML = `<strong>操作说明</strong><p>${suiteConfig.intro}</p>`;
  wrapper.append(intro);

  const grid = document.createElement("div");
  grid.className = "animation-module-grid";

  suiteConfig.modules.forEach((moduleConfig, index) => {
    const module = createAnimationModule(suiteConfig.suiteId, moduleConfig);
    grid.append(module);
    if (index === 0 && !animationState.activeModuleId) {
      setActiveAnimationModule(moduleConfig.id);
    }
  });

  wrapper.append(grid);
  return wrapper;
}

function createAnimationModule(suiteId, config) {
  const module = document.createElement("section");
  module.className = "animation-module";
  module.dataset.animationId = config.id;

  const header = document.createElement("div");
  header.className = "animation-module-header";

  const titleGroup = document.createElement("div");
  const title = document.createElement("h4");
  title.textContent = config.title;
  const description = document.createElement("p");
  description.textContent = config.description;
  titleGroup.append(title, description);

  const badge = createStatusTag("success", "Space 推进");
  header.append(titleGroup, badge);

  const controls = document.createElement("div");
  controls.className = "animation-controls";

  const prevButton = createAnimationButton("上一步");
  const nextButton = createAnimationButton("下一步");
  const resetButton = createAnimationButton("重置");
  resetButton.classList.add("subtle");
  const focusButton = createAnimationButton("聚焦模块");
  focusButton.classList.add("subtle", "animation-focus-button");
  focusButton.dataset.moduleId = config.id;

  const counter = document.createElement("span");
  counter.className = "animation-counter";

  controls.append(prevButton, nextButton, resetButton, focusButton, counter);

  const teacherStrip = document.createElement("div");
  teacherStrip.className = "animation-teacher-strip";

  const currentCard = document.createElement("div");
  currentCard.className = "animation-teacher-card current";
  const currentLabel = document.createElement("span");
  currentLabel.className = "animation-teacher-label";
  currentLabel.textContent = "当前在讲";
  const currentText = document.createElement("strong");
  currentCard.append(currentLabel, currentText);

  const nextCard = document.createElement("div");
  nextCard.className = "animation-teacher-card next";
  const nextLabel = document.createElement("span");
  nextLabel.className = "animation-teacher-label";
  nextLabel.textContent = "下一步会发生什么";
  const nextText = document.createElement("strong");
  nextCard.append(nextLabel, nextText);

  teacherStrip.append(currentCard, nextCard);

  const layout = document.createElement("div");
  layout.className = "animation-layout";

  const visualZone = createAnimationZone("动画区", "visual");
  const explainZone = createAnimationZone("讲解文字区", "explain");
  const debugZone = createAnimationZone("Debug 区", "debug");
  const codeZone = createAnimationZone("代码区", "code");

  layout.append(visualZone.root, explainZone.root, debugZone.root, codeZone.root);
  module.append(header, teacherStrip, controls, layout);

  const runtime = {
    suiteId,
    config,
    root: module,
    step: 0,
    counter,
    prevButton,
    nextButton,
    resetButton,
    focusButton,
    teacherStrip: {
      currentText,
      nextText,
    },
    zones: {
      visual: visualZone.body,
      explain: explainZone.body,
      debug: debugZone.body,
      code: codeZone.body,
    },
  };

  animationState.runtimes.set(config.id, runtime);

  module.addEventListener("click", () => setActiveAnimationModule(config.id));
  prevButton.addEventListener("click", (event) => {
    event.stopPropagation();
    moveAnimationStep(config.id, -1);
  });
  nextButton.addEventListener("click", (event) => {
    event.stopPropagation();
    moveAnimationStep(config.id, 1);
  });
  resetButton.addEventListener("click", (event) => {
    event.stopPropagation();
    resetAnimation(config.id);
  });
  focusButton.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleFocusedAnimationModule(config.id);
  });

  renderAnimationRuntime(runtime);
  return module;
}

function createAnimationButton(label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "animation-button";
  button.textContent = label;
  return button;
}

function createAnimationZone(label, modifier) {
  const root = document.createElement("section");
  root.className = `animation-zone animation-zone-${modifier}`;

  const heading = document.createElement("h5");
  heading.className = "animation-zone-title";
  heading.textContent = label;

  const body = document.createElement("div");
  body.className = "animation-zone-body";

  root.append(heading, body);
  return { root, body };
}

function renderAnimationRuntime(runtime) {
  const snapshot = runtime.config.getSnapshot(runtime.step);
  const nextStep = Math.min(runtime.config.maxStep, runtime.step + 1);
  const nextSnapshot = runtime.config.getSnapshot(nextStep);
  runtime.counter.textContent = `步骤 ${runtime.step}/${runtime.config.maxStep}`;
  runtime.prevButton.disabled = runtime.step === 0;
  runtime.nextButton.disabled = runtime.step >= runtime.config.maxStep;
  runtime.resetButton.disabled = runtime.step === 0;
  runtime.root.classList.toggle("is-active", animationState.activeModuleId === runtime.config.id);
  runtime.teacherStrip.currentText.textContent = snapshot.headline;
  runtime.teacherStrip.nextText.textContent =
    runtime.step >= runtime.config.maxStep ? "当前模块已经完成，可以重置或回退重新讲。" : nextSnapshot.headline;

  runtime.zones.visual.replaceChildren(buildVisualPanel(snapshot.visual));
  runtime.zones.explain.replaceChildren(buildExplainPanel(snapshot));
  runtime.zones.debug.replaceChildren(buildDebugPanel(snapshot.debug));
  runtime.zones.code.replaceChildren(buildCodePanel(snapshot.code));
  updateTeacherToolbarState();
}

function buildVisualPanel(visual) {
  const fragment = document.createDocumentFragment();

  if (visual.lead) {
    const lead = document.createElement("p");
    lead.className = "animation-lead";
    lead.textContent = visual.lead;
    fragment.append(lead);
  }

  if (visual.matrixPanels?.length) {
    fragment.append(buildMatrixPanelGrid(visual.matrixPanels));
  }

  if (visual.matrix) {
    fragment.append(buildMatrixBoard(visual.matrix));
  }

  if (visual.memory) {
    fragment.append(buildMemoryStrip(visual.memory));
  }

  if (visual.cards?.length) {
    fragment.append(buildDiagramCards(visual.cards));
  }

  if (visual.formula) {
    const formula = document.createElement("div");
    formula.className = "animation-formula";
    formula.textContent = visual.formula;
    fragment.append(formula);
  }

  return fragment;
}

function buildMatrixPanelGrid(panels) {
  const wrapper = document.createElement("div");
  wrapper.className = "matrix-panel-grid";

  panels.forEach((panel) => {
    wrapper.append(buildMatrixBoard(panel));
  });

  return wrapper;
}

function buildExplainPanel(snapshot) {
  const wrapper = document.createElement("div");
  wrapper.className = "animation-explain-scroll-content";

  const headline = document.createElement("h6");
  headline.className = "animation-headline";
  headline.textContent = snapshot.headline;
  wrapper.append(headline);

  if (snapshot.badge) {
    const badge = document.createElement("div");
    badge.className = "animation-highlight";
    badge.textContent = snapshot.badge;
    wrapper.append(badge);
  }

  if (snapshot.explanation?.length) {
    const list = document.createElement("ul");
    list.className = "animation-explanation-list";
    snapshot.explanation.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      list.append(li);
    });
    wrapper.append(list);
  }

  return wrapper;
}

function buildDebugPanel(debugItems) {
  const list = document.createElement("dl");
  list.className = "animation-debug-list";

  debugItems.forEach((item) => {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = item.label;
    const dd = document.createElement("dd");
    dd.textContent = item.value;
    row.append(dt, dd);
    list.append(row);
  });

  return list;
}

function buildCodePanel(code) {
  const pre = document.createElement("pre");
  pre.className = "animation-code-block";

  code.lines.forEach((line, index) => {
    const row = document.createElement("div");
    row.className = "code-line";
    if (code.activeLines.includes(index)) {
      row.classList.add("is-active");
    }
    row.textContent = line;
    pre.append(row);
  });

  return pre;
}

function buildMatrixBoard(matrix) {
  const wrapper = document.createElement("div");
  wrapper.className = "matrix-board";

  if (matrix.title) {
    const title = document.createElement("strong");
    title.className = "visual-subtitle";
    title.textContent = matrix.title;
    wrapper.append(title);
  }

  const grid = document.createElement("div");
  grid.className = "matrix-grid";
  grid.style.setProperty("--matrix-cols", String(matrix.cols));
  grid.style.setProperty("--matrix-cell-min", matrix.cols >= 5 ? "52px" : matrix.cols === 4 ? "58px" : "64px");

  matrix.cells.forEach((cell) => {
    const node = document.createElement("div");
    node.className = `matrix-cell ${cell.state || "pending"}`;
    if (cell.state === "row-active") {
      node.classList.add("filled");
    }
    node.innerHTML = `<span class="matrix-label">${cell.label}</span><strong>${cell.value ?? ""}</strong>`;
    grid.append(node);
  });

  wrapper.append(grid);

  if (matrix.caption) {
    const caption = document.createElement("p");
    caption.className = "visual-caption";
    caption.textContent = matrix.caption;
    wrapper.append(caption);
  }

  return wrapper;
}

function buildMemoryStrip(memory) {
  const wrapper = document.createElement("div");
  wrapper.className = "memory-board";

  const title = document.createElement("strong");
  title.className = "visual-subtitle";
  title.textContent = memory.title || "线性内存条";
  wrapper.append(title);

  const strip = document.createElement("div");
  strip.className = "memory-strip";

  memory.cells.forEach((cell) => {
    const node = document.createElement("div");
    node.className = `memory-cell ${cell.state || "pending"}`;
    node.innerHTML = `<span>${cell.label}</span>${cell.detail ? `<small>${cell.detail}</small>` : ""}`;
    strip.append(node);
  });

  wrapper.append(strip);

  if (memory.caption) {
    const caption = document.createElement("p");
    caption.className = "visual-caption";
    caption.textContent = memory.caption;
    wrapper.append(caption);
  }

  return wrapper;
}

function buildDiagramCards(cards) {
  const wrapper = document.createElement("div");
  wrapper.className = "diagram-card-grid";

  cards.forEach((card) => {
    const node = document.createElement("div");
    node.className = `diagram-card ${card.state || "pending"}`;
    node.innerHTML = `<span class="diagram-label">${card.label}</span><strong>${card.value}</strong>${
      card.detail ? `<p>${card.detail}</p>` : ""
    }`;
    wrapper.append(node);
  });

  return wrapper;
}

function setActiveAnimationModule(moduleId) {
  animationState.activeModuleId = moduleId;
  animationState.runtimes.forEach((runtime) => {
    runtime.root.classList.toggle("is-active", runtime.config.id === moduleId);
  });
  updateTeacherToolbarState();
}

function moveAnimationStep(moduleId, delta) {
  const runtime = animationState.runtimes.get(moduleId);
  if (!runtime) {
    return;
  }
  setActiveAnimationModule(moduleId);
  runtime.step = Math.min(runtime.config.maxStep, Math.max(0, runtime.step + delta));
  renderAnimationRuntime(runtime);
  flashAnimationModule(runtime.root);
}

function resetAnimation(moduleId) {
  const runtime = animationState.runtimes.get(moduleId);
  if (!runtime) {
    return;
  }
  setActiveAnimationModule(moduleId);
  runtime.step = 0;
  renderAnimationRuntime(runtime);
  flashAnimationModule(runtime.root);
}

function getActiveAnimationRuntime() {
  if (animationState.activeModuleId && animationState.runtimes.has(animationState.activeModuleId)) {
    return animationState.runtimes.get(animationState.activeModuleId);
  }
  const firstRuntime = animationState.runtimes.values().next().value;
  if (firstRuntime) {
    setActiveAnimationModule(firstRuntime.config.id);
  }
  return firstRuntime || null;
}

function flashAnimationModule(node) {
  node.classList.remove("is-step-flashing");
  void node.offsetWidth;
  node.classList.add("is-step-flashing");
  window.setTimeout(() => {
    node.classList.remove("is-step-flashing");
  }, 420);
}

function setupAnimationHotkeys() {
  document.addEventListener("keydown", (event) => {
    const tagName = event.target?.tagName;
    if (tagName && ["INPUT", "TEXTAREA", "SELECT"].includes(tagName)) {
      return;
    }

    const runtime = getActiveAnimationRuntime();
    if (!runtime && ["Space", "ArrowRight", "ArrowLeft", "KeyR"].includes(event.code)) {
      return;
    }

    if (event.code === "Space" || event.code === "ArrowRight") {
      event.preventDefault();
      moveAnimationStep(runtime.config.id, 1);
      return;
    }

    if (event.code === "ArrowLeft") {
      event.preventDefault();
      moveAnimationStep(runtime.config.id, -1);
      return;
    }

    if (event.code === "KeyR") {
      event.preventDefault();
      resetAnimation(runtime.config.id);
    }
  });
}

function setupLectureTeachingUI() {
  setupTeacherControlHandlers();
  setupQuestionFocus();
  createTeacherKeyboardHint();
  initializeCurrentQuestion();
  let teacherMode = false;
  try {
    teacherMode = window.localStorage.getItem(TEACHER_MODE_STORAGE_KEY) === "true";
  } catch (_error) {
    teacherMode = false;
  }
  setTeacherMode(teacherMode, { persist: false });
  updateTeacherToolbarState();
}

function setupTeacherControlHandlers() {
  document.addEventListener("click", (event) => {
    const teacherButton = event.target.closest("[data-teacher-action]");
    if (teacherButton) {
      event.preventDefault();
      handleTeacherAction(teacherButton);
      return;
    }

    const questionCard = event.target.closest(".question-card");
    if (questionCard?.dataset.questionId) {
      setCurrentQuestion(questionCard.dataset.questionId);
    }
  });
}

function handleTeacherAction(button) {
  const action = button.dataset.teacherAction;
  if (action === "toggle-teacher-mode") {
    setTeacherMode(!teacherState.enabled);
    return;
  }
  if (action === "collapse-all-panels") {
    collapseAllPanels(button.dataset.panelType);
    return;
  }
  if (action === "expand-current-panel") {
    expandCurrentPanel(button.dataset.panelType);
    return;
  }
  if (action === "toggle-focus-current-module") {
    toggleFocusCurrentModule();
    return;
  }
  if (action === "toggle-keyboard-hint") {
    topicAppRoot?.classList.toggle("teacher-keyboard-hidden");
    updateTeacherToolbarState();
  }
}

function createTeacherKeyboardHint() {
  if (!topicAppRoot || topicAppRoot.querySelector(".teacher-keyboard-hint")) {
    return;
  }
  const hint = document.createElement("aside");
  hint.className = "teacher-keyboard-hint";
  hint.innerHTML = `
    <strong>键盘提示</strong>
    <span><kbd>Space</kbd><em>下一步</em></span>
    <span><kbd>←</kbd><em>上一步</em></span>
    <span><kbd>R</kbd><em>重置</em></span>
  `;
  topicAppRoot.append(hint);
}

function setTeacherMode(enabled, options = {}) {
  const { persist = true } = options;
  teacherState.enabled = enabled;
  topicAppRoot?.classList.toggle("teacher-mode", enabled);
  if (!enabled) {
    setFocusedAnimationModule(null, { scroll: false, force: true });
  }
  if (persist) {
    try {
      window.localStorage.setItem(TEACHER_MODE_STORAGE_KEY, String(enabled));
    } catch (_error) {
      // Ignore storage failures so the mode switch still works.
    }
  }
  updateTeacherToolbarState();
}

function initializeCurrentQuestion() {
  const firstQuestion = document.querySelector(".question-card[data-question-id]");
  if (firstQuestion?.dataset.questionId) {
    setCurrentQuestion(firstQuestion.dataset.questionId);
  }
}

function setupQuestionFocus() {
  const current = getCurrentQuestionCard();
  if (!current) {
    return;
  }
  setCurrentQuestion(current.dataset.questionId);
}

function setCurrentQuestion(questionId) {
  teacherState.currentQuestionId = questionId;
  document.querySelectorAll(".question-card[data-question-id]").forEach((card) => {
    card.classList.toggle("is-current", card.dataset.questionId === questionId);
  });
  updateTeacherToolbarState();
}

function getCurrentQuestionCard() {
  return (
    document.querySelector(`.question-card[data-question-id="${teacherState.currentQuestionId}"]`) ||
    document.querySelector(".question-card[data-question-id]")
  );
}

function updateTeacherToolbarState() {
  document.querySelectorAll(".teacher-mode-button").forEach((button) => {
    button.textContent = teacherState.enabled ? "退出教师模式" : "进入教师模式";
    button.setAttribute("aria-pressed", String(teacherState.enabled));
  });

  const lecturePill = document.querySelector('[data-teacher-status="lecture"] strong');
  if (lecturePill) {
    lecturePill.textContent = document.getElementById("page-title")?.textContent || "当前讲次";
  }

  const activeRuntime =
    (animationState.activeModuleId && animationState.runtimes.get(animationState.activeModuleId)) ||
    animationState.runtimes.values().next().value;
  const modulePill = document.querySelector('[data-teacher-status="module"] strong');
  if (modulePill) {
    modulePill.textContent = activeRuntime ? activeRuntime.config.title : "未激活动画模块";
  }

  const currentQuestion = getCurrentQuestionCard();
  const questionPill = document.querySelector('[data-teacher-status="question"] strong');
  if (questionPill) {
    questionPill.textContent = currentQuestion?.querySelector(".question-title")?.textContent || "未选中题目";
  }

  const focusButton = document.querySelector('[data-teacher-action="toggle-focus-current-module"]');
  if (focusButton) {
    focusButton.textContent = teacherState.focusedModuleId ? "退出模块聚焦" : "聚焦当前动画";
  }

  const keyboardButton = document.querySelector('[data-teacher-action="toggle-keyboard-hint"]');
  if (keyboardButton) {
    keyboardButton.textContent = topicAppRoot?.classList.contains("teacher-keyboard-hidden") ? "显示键盘提示" : "隐藏键盘提示";
  }

  animationState.runtimes.forEach((runtime) => {
    if (runtime.focusButton) {
      runtime.focusButton.textContent = teacherState.focusedModuleId === runtime.config.id ? "退出聚焦" : "聚焦模块";
    }
  });
}

function toggleFocusCurrentModule() {
  const runtime = getActiveAnimationRuntime();
  if (!runtime) {
    return;
  }
  toggleFocusedAnimationModule(runtime.config.id);
}

function toggleFocusedAnimationModule(moduleId) {
  if (teacherState.focusedModuleId === moduleId) {
    setFocusedAnimationModule(null);
    return;
  }
  if (!teacherState.enabled) {
    setTeacherMode(true);
  }
  setFocusedAnimationModule(moduleId);
}

function setFocusedAnimationModule(moduleId, options = {}) {
  const { scroll = true, force = false } = options;
  if (!force && teacherState.focusedModuleId === moduleId) {
    return;
  }

  if (moduleId) {
    setActiveAnimationModule(moduleId);
  }

  teacherState.focusedModuleId = moduleId;
  topicAppRoot?.classList.toggle("module-focus-active", Boolean(moduleId));

  document.querySelectorAll(".section-block").forEach((node) => node.classList.remove("contains-focused-module"));
  document.querySelectorAll(".topic-card").forEach((node) => node.classList.remove("contains-focused-module"));

  animationState.runtimes.forEach((runtime) => {
    const isFocused = runtime.config.id === moduleId;
    runtime.root.classList.toggle("is-focused", isFocused);
    if (isFocused) {
      runtime.root.closest(".section-block")?.classList.add("contains-focused-module");
      runtime.root.closest(".topic-card")?.classList.add("contains-focused-module");
      if (scroll) {
        runtime.root.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  });

  updateTeacherToolbarState();
}

function collapseAllPanels(panelType) {
  document.querySelectorAll(`.question-panel[data-panel-type="${panelType}"]`).forEach((panel) => {
    setQuestionPanelVisibility(panel, false);
  });
}

function expandCurrentPanel(panelType) {
  collapseAllPanels(panelType);
  const currentQuestion = getCurrentQuestionCard();
  if (!currentQuestion) {
    return;
  }
  const targetPanel = currentQuestion.querySelector(`.question-panel[data-panel-type="${panelType}"]`);
  if (targetPanel) {
    setQuestionPanelVisibility(targetPanel, true);
  }
}

function setQuestionPanelVisibility(panel, visible) {
  panel.classList.toggle("is-hidden", !visible);
  const button = document.querySelector(`.toggle-button[data-target="${panel.id}"]`);
  if (button) {
    button.textContent = visible ? button.dataset.labelHide : button.dataset.labelShow;
  }
}

function setupToggles() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".toggle-button");
    if (!button) {
      return;
    }

    const panel = document.getElementById(button.dataset.target);
    if (!panel) {
      return;
    }

    const questionCard = button.closest(".question-card");
    if (questionCard?.dataset.questionId) {
      setCurrentQuestion(questionCard.dataset.questionId);
    }

    const shouldShow = panel.classList.contains("is-hidden");
    setQuestionPanelVisibility(panel, shouldShow);
  });
}

function setupNavHighlight() {
  const navLinks = [...document.querySelectorAll(".outline-anchor[data-target]")];
  const linkMap = new Map(navLinks.map((link) => [link.dataset.target, link]));
  const targets = [...document.querySelectorAll(".observe-target")].filter((target) => linkMap.has(target.id));
  if (!navLinks.length || !targets.length) {
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];

      if (!visible) {
        return;
      }

      navLinks.forEach((link) => link.classList.remove("is-active"));
      const link = linkMap.get(visible.target.id);
      if (link) {
        link.classList.add("is-active");
      }
    },
    {
      rootMargin: "-20% 0px -60% 0px",
      threshold: [0.1, 0.2, 0.4, 0.6],
    },
  );

  targets.forEach((target) => observer.observe(target));
}

function scrollToHashTarget() {
  const hash = window.location.hash.slice(1);
  if (!hash) {
    return;
  }

  const target = document.getElementById(hash);
  if (!target) {
    return;
  }

  window.requestAnimationFrame(() => {
    target.scrollIntoView({ block: "start", behavior: "auto" });
  });
}

function buildLectureOneMemorySnapshot(step) {
  const rows = 3;
  const cols = 4;
  const total = rows * cols;
  const currentIndex = step > 0 ? step - 1 : null;
  const current = currentIndex === null ? null : cellMeta(currentIndex, cols);

  return {
    headline: current ? `当前正在开辟 ${current.label}` : "准备从 a[0][0] 开始按行开辟二维数组",
    badge: current ? `步骤 ${step}：${current.label}` : "按行连续开辟",
    explanation: current
      ? [
          `当前的行索引 i = ${current.row}，列索引 j = ${current.col}。`,
          `二维数组会先把第 ${current.row + 1} 行从左到右开辟完，再进入下一行。`,
          `下面的线性内存条和二维网格是一一映射的，当前高亮位置相同。`,
        ]
      : [
          "声明 `int a[3][4];` 后，一共需要开辟 12 个整型格子。",
          "每按一次空格，就沿着行优先顺序开辟一个新单元。",
        ],
    debug: [
      { label: "当前 i", value: current ? String(current.row) : "-" },
      { label: "当前 j", value: current ? String(current.col) : "-" },
      { label: "currentCell", value: current ? current.label : "尚未开始" },
      { label: "memoryIndex", value: current ? String(current.index) : "-" },
      { label: "说明", value: "按行连续开辟" },
    ],
    code: {
      lines: ["int a[3][4];", "// row-major: 先行后列，线性内存下标 = i * 4 + j"],
      activeLines: [0, 1],
    },
    visual: {
      lead: "二维网格和线性内存条同步高亮，帮助学生把二维视角和内存视角对齐。",
      matrix: {
        title: "3 × 4 网格",
        rows,
        cols,
        caption: current
          ? `当前正在开辟 ${current.label}，位于第 ${current.row + 1} 行第 ${current.col + 1} 列。`
          : "等待开始后，将从第 1 行第 1 列开始。",
        cells: Array.from({ length: total }, (_, index) => {
          const meta = cellMeta(index, cols);
          let state = "pending";
          if (step > 0 && index < step) {
            state = "filled";
          }
          if (current && current.index === index) {
            state = "active";
          }
          return {
            label: meta.label,
            value: step > 0 && index < step ? `mem[${index}]` : "未开辟",
            state,
          };
        }),
      },
      memory: {
        title: "线性内存条",
        caption: "内存顺序固定是 0,1,2,3,4...，二维下标只是这条线性空间的映射视图。",
        cells: Array.from({ length: total }, (_, index) => {
          const meta = cellMeta(index, cols);
          let state = "pending";
          if (step > 0 && index < step) {
            state = "filled";
          }
          if (current && current.index === index) {
            state = "active";
          }
          return {
            label: `mem[${index}]`,
            detail: `${meta.label}`,
            state,
          };
        }),
      },
    },
  };
}

function buildLectureOneAccessSnapshot(step) {
  const cases = [
    { row: 0, col: 0 },
    { row: 1, col: 2 },
    { row: 2, col: 3 },
    { row: 1, col: 0 },
  ];
  const current = step > 0 ? cases[Math.min(step - 1, cases.length - 1)] : null;

  return {
    headline: current ? `当前访问表达式：a[${current.row}][${current.col}]` : "先准备几个典型访问例子",
    badge: current ? `第 ${current.row + 1} 行，第 ${current.col + 1} 列` : "先行后列",
    explanation: current
      ? [
          `表达式 a[${current.row}][${current.col}] 的第一个下标先定位“第 ${current.row + 1} 行”。`,
          `第二个下标再定位“第 ${current.col + 1} 列”。`,
          "二维数组访问一定是先行后列，不能把人类描述直接抄成代码下标。",
        ]
      : [
          "按空格会依次切换 a[0][0]、a[1][2]、a[2][3] 等例子。",
          "每一步都同时显示表达式、题面位置和高亮格子。",
        ],
    debug: [
      { label: "当前表达式", value: current ? `a[${current.row}][${current.col}]` : "-" },
      { label: "题面说法", value: current ? `第 ${current.row + 1} 行，第 ${current.col + 1} 列` : "-" },
      { label: "currentCell", value: current ? `(${current.row}, ${current.col})` : "尚未选择" },
      { label: "访问规则", value: "先行后列，下标从 0 开始" },
    ],
    code: {
      lines: [
        "// 题面“第 x 行第 y 列”",
        "row = x - 1;",
        "col = y - 1;",
        "value = a[row][col];",
      ],
      activeLines: current ? [1, 2, 3] : [0],
    },
    visual: {
      lead: "网格上的高亮顺序就是“先看行，再看列”的执行结果。",
      matrix: {
        title: "访问坐标示意",
        rows: 3,
        cols: 4,
        caption: current ? `当前高亮的是第 ${current.row + 1} 行第 ${current.col + 1} 列。` : "等待开始后会依次切换不同坐标。",
        cells: Array.from({ length: 12 }, (_, index) => {
          const meta = cellMeta(index, 4);
          const active = current && current.row === meta.row && current.col === meta.col;
          return {
            label: meta.label,
            value: `第 ${meta.row + 1} 行 / 第 ${meta.col + 1} 列`,
            state: active ? "active" : "pending",
          };
        }),
      },
      formula: current ? `a[${current.row}][${current.col}]  ->  第 ${current.row + 1} 行，第 ${current.col + 1} 列` : "表达式与题面位置会同步显示在这里。",
    },
  };
}

function buildLectureOneTraversalSnapshot(step) {
  const order = lectureOneTraversalValues.flatMap((row, rowIndex) =>
    row.map((value, colIndex) => ({ row: rowIndex, col: colIndex, value })),
  );
  const totalVisits = order.length;
  let phase = "intro";
  let current = null;
  let visitedCount = 0;

  if (step >= 1 && step <= totalVisits) {
    phase = "input";
    current = order[step - 1];
    visitedCount = step - 1;
  } else if (step > totalVisits && step <= totalVisits * 2) {
    phase = "output";
    current = order[step - totalVisits - 1];
    visitedCount = step - totalVisits - 1;
  }

  const phaseLabel = phase === "input" ? "输入阶段" : phase === "output" ? "输出阶段" : "准备开始";
  const codeLines = [
    "for (int i = 0; i < 2; i++) {",
    "  for (int j = 0; j < 3; j++) {",
    "    cin >> a[i][j];",
    "  }",
    "}",
    "for (int i = 0; i < 2; i++) {",
    "  for (int j = 0; j < 3; j++) {",
    "    cout << a[i][j] << ' ';",
    "  }",
    "  cout << endl;",
    "}",
  ];

  return {
    headline: current ? `${phaseLabel}：访问 a[${current.row}][${current.col}]` : "准备进入输入输出遍历",
    badge: `${phaseLabel}${current ? ` · i=${current.row}, j=${current.col}` : ""}`,
    explanation: current
      ? [
          `当前执行到双重循环中的 a[${current.row}][${current.col}]。`,
          `外层循环控制 i（行），内层循环控制 j（列）。`,
          phase === "input"
            ? `这一步把值 ${current.value} 读入当前格子。`
            : `这一步把当前格子的值 ${current.value} 输出到屏幕。`,
        ]
      : [
          "前 6 步演示输入，后 6 步演示输出。",
          "每一步都显示当前执行行、i/j、currentCell 和 currentValue。",
        ],
    debug: [
      { label: "阶段", value: phaseLabel },
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "currentCell", value: current ? `a[${current.row}][${current.col}]` : "尚未开始" },
      { label: "currentValue", value: current ? String(current.value) : "-" },
    ],
    code: {
      lines: codeLines,
      activeLines:
        phase === "input"
          ? [0, 1, 2]
          : phase === "output"
            ? [5, 6, 7]
            : [0, 5],
    },
    visual: {
      lead: "颜色区分输入完成、正在访问和输出完成的格子，帮助学生把遍历顺序看清楚。",
      matrix: {
        title: "2 × 3 遍历网格",
        rows: 2,
        cols: 3,
        caption: current ? `当前访问 ${phase === "input" ? "写入" : "输出"}的格子是 a[${current.row}][${current.col}]。` : "等待开始后会从 a[0][0] 进入。",
        cells: Array.from({ length: order.length }, (_, index) => {
          const meta = order[index];
          let state = "pending";
          if (phase === "input" && index < visitedCount) {
            state = "filled";
          }
          if (phase === "output") {
            state = index < visitedCount ? "echoed" : "filled";
          }
          if (current && current.row === meta.row && current.col === meta.col) {
            state = "active";
          }
          return {
            label: `a[${meta.row}][${meta.col}]`,
            value: String(meta.value),
            state,
          };
        }),
      },
      formula: current ? `currentCell = a[${current.row}][${current.col}]，currentValue = ${current.value}` : "debug 面板会同步显示 currentCell / currentValue。",
    },
  };
}

function buildLectureTwoRowMajorSnapshot(step) {
  const total = lectureTwoMatrixValues.length * lectureTwoMatrixValues[0].length;
  const currentIndex = step > 0 ? step - 1 : null;
  const current = currentIndex === null ? null : cellMeta(currentIndex, 4);
  const currentValue = current ? lectureTwoMatrixValues[current.row][current.col] : null;

  return {
    headline: current ? `当前映射 ${current.label} -> mem[${current.index}]` : "准备按 row-major 顺序扫描二维数组",
    badge: current ? `线性下标 = ${current.row} * 4 + ${current.col} = ${current.index}` : "按行存储 / row-major",
    explanation: current
      ? [
          `二维坐标 ${current.label} 在线性空间中对应 mem[${current.index}]。`,
          "每进入下一列，线性下标加 1；一行结束后继续衔接到下一行。",
          "这就是“按行存储”的具体效果。",
        ]
      : [
          "按空格依次走过全部 12 个格子。",
          "每一步同时高亮网格位置和内存位置，并显示换算公式。",
        ],
    debug: [
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "currentCell", value: current ? current.label : "尚未开始" },
      { label: "linearIndex", value: current ? String(current.index) : "-" },
      { label: "currentValue", value: currentValue === null ? "-" : String(currentValue) },
    ],
    code: {
      lines: [
        "// row-major 存储",
        "linearIndex = i * cols + j;",
        "memory[linearIndex] <-> a[i][j];",
      ],
      activeLines: current ? [1, 2] : [0],
    },
    visual: {
      lead: "这是第 2 讲的核心：二维数组本质上仍是一条线性的连续内存。",
      matrix: {
        title: "二维数组网格",
        rows: 3,
        cols: 4,
        caption: "扫描顺序始终先走完一整行，再进入下一行。",
        cells: lectureTwoMatrixValues.flatMap((row, rowIndex) =>
          row.map((value, colIndex) => {
            const index = rowIndex * 4 + colIndex;
            const active = current && current.row === rowIndex && current.col === colIndex;
            return {
              label: `a[${rowIndex}][${colIndex}]`,
              value: String(value),
              state: active ? "active" : step > 0 && index < step ? "filled" : "pending",
            };
          }),
        ),
      },
      memory: {
        title: "按行铺开的一维内存",
        caption: "内存顺序固定为 1,2,3,4,5...；二维访问只是把它解释成 3 行 4 列。",
        cells: lectureTwoMatrixValues.flatMap((row, rowIndex) =>
          row.map((value, colIndex) => {
            const index = rowIndex * 4 + colIndex;
            return {
              label: `mem[${index}]`,
              detail: String(value),
              state: current && current.index === index ? "active" : step > 0 && index < step ? "filled" : "pending",
            };
          }),
        ),
      },
    },
  };
}

function buildLectureTwoWholeRowSnapshot(step) {
  const rowIndex = step > 0 ? step - 1 : null;
  const rowValues = rowIndex === null ? [] : lectureTwoMatrixValues[rowIndex];

  return {
    headline: rowIndex === null ? "准备依次选中 a[0]、a[1]、a[2]" : `当前高亮整行：a[${rowIndex}]`,
    badge: rowIndex === null ? "a[i] 表示一整行" : `a[${rowIndex}] = [${rowValues.join(", ")}]`,
    explanation: rowIndex === null
      ? [
          "第 2 讲要让学生知道：a[i] 不是单个元素，而是一整行。",
          "按空格会依次高亮第 0 行、第 1 行和第 2 行。",
        ]
      : [
          `a[${rowIndex}] 表示第 ${rowIndex + 1} 行整行数据。`,
          `这一行包含 ${rowValues.map((_value, colIndex) => `a[${rowIndex}][${colIndex}]`).join("、")}。`,
          "从“整行”理解二维数组，后面做按行统计会更自然。",
        ],
    debug: [
      { label: "当前表达式", value: rowIndex === null ? "-" : `a[${rowIndex}]` },
      { label: "含义", value: rowIndex === null ? "等待开始" : `第 ${rowIndex + 1} 行` },
      { label: "元素个数", value: rowIndex === null ? "-" : String(rowValues.length) },
      { label: "本行元素", value: rowIndex === null ? "-" : rowValues.join(", ") },
    ],
    code: {
      lines: [
        "int sumRow(int a[][4], int row) {",
        "  int sum = 0;",
        "  for (int j = 0; j < 4; j++) {",
        "    sum += a[row][j];",
        "  }",
        "  return sum;",
        "}",
      ],
      activeLines: rowIndex === null ? [0] : [2, 3, 4],
    },
    visual: {
      lead: "整行高亮和整段内存高亮会同步发生，帮助学生理解 a[i] 与 row segment 的对应关系。",
      matrix: {
        title: "高亮整行",
        rows: 3,
        cols: 4,
        caption: rowIndex === null ? "等待开始后会依次高亮三行。" : `当前整行是 a[${rowIndex}]。`,
        cells: lectureTwoMatrixValues.flatMap((row, currentRow) =>
          row.map((value, colIndex) => ({
            label: `a[${currentRow}][${colIndex}]`,
            value: String(value),
            state: rowIndex === null ? "pending" : currentRow === rowIndex ? "row-active" : "pending",
          })),
        ),
      },
      memory: {
        title: "对应的一段线性内存",
        caption: "一整行在内存里也是连续的一整段。",
        cells: lectureTwoMatrixValues.flatMap((row, currentRow) =>
          row.map((value, colIndex) => ({
            label: `mem[${currentRow * 4 + colIndex}]`,
            detail: String(value),
            state: rowIndex === null ? "pending" : currentRow === rowIndex ? "row-active" : "pending",
          })),
        ),
      },
    },
  };
}

function buildLectureTwoFunctionParamSnapshot(step) {
  const validForms = [
    "void print(int arr[3][4])",
    "void print(int arr[][4])",
    "void print(int (*arr)[4])",
  ];
  const missingColumn = "void print(int arr[][])";

  let headline = "准备解释二维数组为什么必须写列数";
  let badge = "列数决定偏移";
  let explanation = [
    "这一组不做复杂执行器，而是用图示说明参数声明与地址计算的关系。",
    "按空格会从调用方矩阵，推进到合法声明、非法声明和指针形式。",
  ];
  let debug = [
    { label: "当前关注点", value: "为什么列数要明确" },
    { label: "cols", value: "4" },
    { label: "offset 示例", value: "arr[1][2] -> 1 * 4 + 2" },
  ];
  let cards = [
    { label: "调用方", value: "int a[3][4]", detail: "真实矩阵有 4 列", state: "active" },
    { label: "合法写法", value: validForms[1], detail: "保留列数 4", state: "success" },
    { label: "错误写法", value: missingColumn, detail: "缺少列数，无法算偏移", state: "warning" },
  ];
  let formula = "arr[i][j] 的地址 = base + (i * cols + j) * sizeof(int)";
  let activeLines = [0];

  if (step === 1) {
    headline = "先看调用方：真实传入的是一个 3 × 4 的矩阵";
    explanation = [
      "函数参数名接收到的不是“魔法二维表”，而是一块已经按行展开的连续内存。",
      "要正确访问 arr[i][j]，函数必须知道每一行有多少列。",
    ];
    activeLines = [0, 1];
  } else if (step === 2) {
    headline = "合法写法一：arr[][4] 保留了列数 4";
    explanation = [
      "`arr[][4]` 虽然省略了行数，但保留了列数 4，所以偏移仍然可算。",
      "当访问 arr[1][2] 时，编译器能算出 `1 * 4 + 2`。",
    ];
    cards = [
      { label: "调用方", value: "int a[3][4]", detail: "真实矩阵有 4 列", state: "filled" },
      { label: "合法写法", value: validForms[1], detail: "保留列数 4，偏移可算", state: "active" },
      { label: "错误写法", value: missingColumn, detail: "还没对比到这里", state: "pending" },
    ];
    activeLines = [2];
  } else if (step === 3) {
    headline = "错误写法：arr[][] 缺少列数，编译器没法算偏移";
    explanation = [
      "访问 arr[1][2] 时，需要先跳过一整行。",
      "如果不知道一行有几列，就不能计算要跳过多少个元素，所以这种写法不合法。",
    ];
    cards = [
      { label: "调用方", value: "int a[3][4]", detail: "真实矩阵有 4 列", state: "filled" },
      { label: "合法写法", value: validForms[1], detail: "可以算出偏移", state: "filled" },
      { label: "错误写法", value: missingColumn, detail: "缺列数，无法定位 arr[i][j]", state: "active warning" },
    ];
    activeLines = [3];
  } else if (step === 4) {
    headline = "合法写法二：(*arr)[4] 本质上也是“每行 4 个元素”";
    explanation = [
      "`int (*arr)[4]` 表示 arr 指向“包含 4 个 int 的一整行”。",
      "不管写成 `arr[][4]` 还是 `(*arr)[4]`，关键都在于列数 4 明确存在。",
    ];
    cards = [
      { label: "调用方", value: "int a[3][4]", detail: "真实矩阵有 4 列", state: "filled" },
      { label: "合法写法", value: validForms[2], detail: "指向一整行，列数仍然明确", state: "active success" },
      { label: "错误写法", value: missingColumn, detail: "缺列数，仍然不合法", state: "warning" },
    ];
    activeLines = [4];
  }

  return {
    headline,
    badge,
    explanation,
    debug,
    code: {
      lines: [
        "int a[3][4];               // 调用方真实矩阵",
        "arr[1][2] -> 1 * 4 + 2    // 偏移计算依赖 cols",
        "void print(int arr[][4]); // 合法：列数明确",
        "void print(int arr[][]);  // 非法：列数缺失",
        "void print(int (*arr)[4]); // 合法：一行 4 个元素",
      ],
      activeLines,
    },
    visual: {
      lead: "这组图解的重点不是语法死记，而是“列数决定偏移”。",
      matrix: {
        title: "调用方矩阵",
        rows: 3,
        cols: 4,
        caption: "调用方真实传入的是 3 行 4 列。",
        cells: lectureTwoMatrixValues.flatMap((row, rowIndex) =>
          row.map((value, colIndex) => ({
            label: `a[${rowIndex}][${colIndex}]`,
            value: String(value),
            state: rowIndex === 1 && colIndex === 2 ? "active" : "filled",
          })),
        ),
      },
      cards,
      formula,
    },
  };
}

function buildLectureThreeSumSnapshot(step) {
  const order = flattenMatrixEntries(lectureThreeStatsValues);
  const current = step > 0 ? order[step - 1] : null;
  const visitedValues = order.slice(0, Math.max(0, step - 1)).map((entry) => entry.value);
  const sumBefore = sumNumberList(visitedValues);
  const sumAfter = current ? sumBefore + current.value : 0;

  return {
    headline: current ? `当前执行 sum += a[${current.row}][${current.col}]` : "准备从 a[0][0] 开始做整表累加",
    badge: current ? `sum：${sumBefore} -> ${sumAfter}` : "总和累加 / row by row",
    explanation: current
      ? [
          `当前访问位置是 a[${current.row}][${current.col}]，值为 ${current.value}。`,
          `这一步先读出旧 sum = ${sumBefore}，再执行加法得到新 sum = ${sumAfter}。`,
          "双重循环仍然是外层走行、内层走列，统计变量只是跟着访问过程同步变化。",
        ]
      : [
          "总和题的核心动作就是：遍历每个格子，然后执行 `sum += a[i][j]`。",
          "按空格后会逐格高亮，并同步显示 sum 的旧值和新值。",
        ],
    debug: [
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "currentCell", value: current ? current.label : "尚未开始" },
      { label: "currentValue", value: current ? String(current.value) : "-" },
      { label: "sum", value: current ? `${sumBefore} -> ${sumAfter}` : "0" },
    ],
    code: {
      lines: [
        "int sum = 0;",
        "for (int i = 0; i < 3; i++) {",
        "  for (int j = 0; j < 3; j++) {",
        "    sum += a[i][j];",
        "  }",
        "}",
      ],
      activeLines: current ? [1, 2, 3] : [0],
    },
    visual: {
      lead: "这一组先只看一个统计量 sum，让学生把“访问顺序”和“变量更新”绑定起来。",
      matrix: {
        title: "3 × 3 总和累加网格",
        rows: 3,
        cols: 3,
        caption: current
          ? `当前访问 ${current.label}，执行前 sum = ${sumBefore}，执行后 sum = ${sumAfter}。`
          : "等待开始后会按行优先顺序逐格累加。",
        cells: buildVisitedMatrixCells(lectureThreeStatsValues, step, current),
      },
      cards: current
        ? [
            { label: "旧 sum", value: String(sumBefore), state: "filled" },
            { label: "当前值", value: String(current.value), state: "active" },
            { label: "新 sum", value: String(sumAfter), state: "success" },
          ]
        : [
            { label: "统计目标", value: "sum", detail: "整张表所有元素求和", state: "active" },
            { label: "初值", value: "0", detail: "累加前先清零", state: "filled" },
          ],
      formula: current ? `sum = ${sumBefore} + ${current.value} = ${sumAfter}` : "每访问一个格子，就做一次 sum += a[i][j]。",
    },
  };
}

function buildLectureThreeExtremaSnapshot(step) {
  const order = flattenMatrixEntries(lectureThreeStatsValues);
  const current = step > 0 ? order[step - 1] : null;
  const baseline = order[0].value;
  const processedBefore = order.slice(0, Math.max(1, step - 1)).map((entry) => entry.value);
  const mxBefore = step === 0 ? baseline : Math.max(...processedBefore);
  const mnBefore = step === 0 ? baseline : Math.min(...processedBefore);
  const mxAfter = current ? Math.max(mxBefore, current.value) : baseline;
  const mnAfter = current ? Math.min(mnBefore, current.value) : baseline;
  const updateMx = Boolean(current && current.value > mxBefore);
  const updateMn = Boolean(current && current.value < mnBefore);

  let updateText = "本步不更新";
  if (updateMx && updateMn) {
    updateText = "同时更新 mx 和 mn";
  } else if (updateMx) {
    updateText = "更新 mx";
  } else if (updateMn) {
    updateText = "更新 mn";
  }

  const activeLines = current ? [2, 3] : [0, 1];
  if (updateMx) {
    activeLines.push(4, 5);
  }
  if (updateMn) {
    activeLines.push(7, 8);
  }

  return {
    headline: current ? `当前比较 ${current.label} = ${current.value}` : "准备开始逐格维护 mx 和 mn",
    badge: current ? `${updateText} · mx=${mxAfter}, mn=${mnAfter}` : "最值更新 / compare then assign",
    explanation: current
      ? [
          `进入 a[${current.row}][${current.col}] 后，先把当前值 ${current.value} 与旧 mx=${mxBefore}、旧 mn=${mnBefore} 比较。`,
          updateMx ? `因为 ${current.value} > ${mxBefore}，所以 mx 更新成 ${mxAfter}。` : `因为 ${current.value} 不大于 ${mxBefore}，所以 mx 保持不变。`,
          updateMn ? `因为 ${current.value} < ${mnBefore}，所以 mn 更新成 ${mnAfter}。` : `因为 ${current.value} 不小于 ${mnBefore}，所以 mn 保持不变。`,
        ]
      : [
          "最值题的核心不是遍历本身，而是每一步都要判断“是否触发更新”。",
          "这里把当前元素、旧值和新值并排展示，帮助学生理解更新条件。",
        ],
    debug: [
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "currentValue", value: current ? String(current.value) : "-" },
      { label: "mx", value: current ? `${mxBefore} -> ${mxAfter}` : String(baseline) },
      { label: "mn", value: current ? `${mnBefore} -> ${mnAfter}` : String(baseline) },
      { label: "updateStatus", value: current ? updateText : "等待开始" },
    ],
    code: {
      lines: [
        "int mx = a[0][0];",
        "int mn = a[0][0];",
        "for (int i = 0; i < 3; i++) {",
        "  for (int j = 0; j < 3; j++) {",
        "    if (a[i][j] > mx) {",
        "      mx = a[i][j];",
        "    }",
        "    if (a[i][j] < mn) {",
        "      mn = a[i][j];",
        "    }",
        "  }",
        "}",
      ],
      activeLines,
    },
    visual: {
      lead: "当前格子负责“提问”，mx / mn 负责“记住目前最优答案”。",
      matrix: {
        title: "最值更新网格",
        rows: 3,
        cols: 3,
        caption: current
          ? `${current.label} = ${current.value}，本步${updateText}。`
          : `初始化时先把 mx 和 mn 都设成 ${baseline}。`,
        cells: buildVisitedMatrixCells(lectureThreeStatsValues, step, current),
      },
      cards: current
        ? [
            { label: "当前元素", value: String(current.value), state: "active" },
            { label: "mx", value: `${mxBefore} -> ${mxAfter}`, state: updateMx ? "success" : "filled" },
            { label: "mn", value: `${mnBefore} -> ${mnAfter}`, state: updateMn ? "success" : "filled" },
          ]
        : [
            { label: "初始 mx", value: String(baseline), state: "filled" },
            { label: "初始 mn", value: String(baseline), state: "filled" },
          ],
      formula: current
        ? `mx = max(${mxBefore}, ${current.value}) = ${mxAfter}；mn = min(${mnBefore}, ${current.value}) = ${mnAfter}`
        : "先用 a[0][0] 初始化 mx 和 mn，再逐格比较。",
    },
  };
}

function buildLectureThreeConditionalCountSnapshot(step) {
  const order = flattenMatrixEntries(lectureThreeStatsValues);
  const current = step > 0 ? order[step - 1] : null;
  const visitedBefore = order.slice(0, Math.max(0, step - 1));
  const cntBefore = visitedBefore.filter((entry) => entry.value % 2 === 0).length;
  const conditionResult = Boolean(current && current.value % 2 === 0);
  const cntAfter = current ? cntBefore + (conditionResult ? 1 : 0) : 0;

  return {
    headline: current ? `当前判断 ${current.label} = ${current.value} 是否满足条件` : "准备开始做条件计数",
    badge: current ? `condition = ${conditionResult ? "true" : "false"} · cnt：${cntBefore} -> ${cntAfter}` : "条件计数 / 统计偶数个数",
    explanation: current
      ? [
          `当前元素是 ${current.value}，本模块的条件是“是否为偶数”。`,
          conditionResult ? `因为 ${current.value} % 2 == 0，所以 cnt 从 ${cntBefore} 增加到 ${cntAfter}。` : `因为 ${current.value} % 2 != 0，所以 cnt 保持 ${cntAfter} 不变。`,
          "计数题的关键是：先判断条件，再决定是否执行 `cnt++`。",
        ]
      : [
          "这里选择“统计偶数个数”作为代表例子，后面可以平移到“大于某值”“等于某值”等计数题。",
          "按空格后，每一步都会告诉学生条件是否成立、cnt 是否增加。",
        ],
    debug: [
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "currentValue", value: current ? String(current.value) : "-" },
      { label: "conditionResult", value: current ? String(conditionResult) : "-" },
      { label: "cnt", value: current ? `${cntBefore} -> ${cntAfter}` : "0" },
    ],
    code: {
      lines: [
        "int cnt = 0;",
        "for (int i = 0; i < 3; i++) {",
        "  for (int j = 0; j < 3; j++) {",
        "    if (a[i][j] % 2 == 0) {",
        "      cnt++;",
        "    }",
        "  }",
        "}",
      ],
      activeLines: current ? (conditionResult ? [1, 2, 3, 4] : [1, 2, 3]) : [0],
    },
    visual: {
      lead: "条件计数的本质是“当前格子先过筛子，再决定是否给 cnt 加 1”。",
      matrix: {
        title: "条件计数网格",
        rows: 3,
        cols: 3,
        caption: current
          ? `${current.label} = ${current.value}，本步条件${conditionResult ? "成立" : "不成立"}。`
          : "等待开始后会逐格判断“是不是偶数”。",
        cells: buildVisitedMatrixCells(lectureThreeStatsValues, step, current),
      },
      cards: current
        ? [
            { label: "当前元素", value: String(current.value), state: "active" },
            { label: "条件结果", value: conditionResult ? "成立" : "不成立", state: conditionResult ? "success" : "warning" },
            { label: "cnt", value: `${cntBefore} -> ${cntAfter}`, state: conditionResult ? "success" : "filled" },
          ]
        : [
            { label: "计数变量", value: "cnt = 0", detail: "条件成立时才增加", state: "active" },
          ],
      formula: current
        ? `${current.value} % 2 == 0 -> ${conditionResult ? "true" : "false"}；cnt = ${cntAfter}`
        : "只有条件成立时，才执行 cnt++。",
    },
  };
}

function buildLectureThreeFixedWindowSnapshot(step) {
  const cases = buildWindowCases(lectureThreeWindowValues, [2, 3]);
  const current = step > 0 ? cases[step - 1] : null;
  const totalBySize = {
    2: cases.filter((item) => item.size === 2).length,
    3: cases.filter((item) => item.size === 3).length,
  };

  return {
    headline: current
      ? `当前考察 ${current.size}×${current.size} 窗口，左上角在 (${current.row}, ${current.col})`
      : "准备演示 2×2 和 3×3 固定窗口的合法移动",
    badge: current
      ? `${current.size}×${current.size} 窗口 · 左上角 a[${current.row}][${current.col}] · localSum = ${current.localSum}`
      : "固定窗口 / 枚举左上角",
    explanation: current
      ? [
          `当前窗口大小是 ${current.size}×${current.size}，左上角固定在 a[${current.row}][${current.col}]。`,
          `窗口中的元素有：${current.entries.map((entry) => `${entry.label}=${entry.value}`).join("、")}。`,
          `这一步的局部统计值 localSum = ${current.entries.map((entry) => entry.value).join(" + ")} = ${current.localSum}。`,
        ]
      : [
          "本模块先完整走完全部 2×2 合法左上角，再继续走 3×3 合法左上角。",
          `在这张 4×4 矩阵上，2×2 一共有 ${totalBySize[2]} 个合法窗口，3×3 一共有 ${totalBySize[3]} 个合法窗口。`,
        ],
    debug: [
      { label: "windowSize", value: current ? `${current.size}x${current.size}` : "2x2 then 3x3" },
      { label: "leftTop", value: current ? `(${current.row}, ${current.col})` : "-" },
      { label: "currentWindow", value: current ? current.entries.map((entry) => entry.label).join(", ") : "尚未开始" },
      { label: "windowValues", value: current ? current.entries.map((entry) => entry.value).join(", ") : "-" },
      { label: "localSum", value: current ? String(current.localSum) : "-" },
    ],
    code: {
      lines: [
        "int K = 2; // 或 3",
        "for (int i = 0; i + K - 1 < rows; i++) {",
        "  for (int j = 0; j + K - 1 < cols; j++) {",
        "    int localSum = 0;",
        "    for (int x = i; x < i + K; x++) {",
        "      for (int y = j; y < j + K; y++) {",
        "        localSum += a[x][y];",
        "      }",
        "    }",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2, 3, 4, 5, 6] : [0],
    },
    visual: {
      lead: "固定窗口题不是逐格扫，而是枚举每一个合法左上角，再在窗口内部做局部统计。",
      matrix: {
        title: "4 × 4 固定窗口矩阵",
        rows: 4,
        cols: 4,
        caption: current
          ? `当前高亮的是 ${current.size}×${current.size} 窗口，左上角是 a[${current.row}][${current.col}]。`
          : "等待开始后会先枚举 2×2，再枚举 3×3。",
        cells: buildWindowMatrixCells(lectureThreeWindowValues, current),
      },
      cards: current
        ? [
            { label: "窗口大小", value: `${current.size}×${current.size}`, state: "active" },
            { label: "窗口元素", value: current.entries.map((entry) => entry.value).join(", "), detail: current.entries.map((entry) => entry.label).join(" / "), state: "filled" },
            { label: "局部和", value: String(current.localSum), state: "success" },
          ]
        : [
            { label: "2×2", value: `${totalBySize[2]} 个合法左上角`, state: "filled" },
            { label: "3×3", value: `${totalBySize[3]} 个合法左上角`, state: "filled" },
          ],
      formula: current
        ? `${current.entries.map((entry) => entry.value).join(" + ")} = ${current.localSum}`
        : "固定窗口的第一步永远是：确定窗口大小 K，再枚举每个合法左上角。",
    },
  };
}

function buildLectureFourCharInputSnapshot(step) {
  const matrix = rowsToCharMatrix(lectureFourInputRows);
  const order = flattenMatrixEntries(matrix);
  const current = step > 0 ? order[step - 1] : null;

  return {
    headline: current ? `当前把 '${current.value}' 写入 ${current.label}` : "准备把字符串行逐字符写入字符网格",
    badge: current
      ? `第 ${current.row + 1} 行，第 ${current.col + 1} 个字符 -> ${current.label}`
      : "字符网格读入 / string row -> char grid",
    explanation: current
      ? [
          `当前读取的是第 ${current.row + 1} 行字符串中的第 ${current.col + 1} 个字符 '${current.value}'。`,
          `读入整行字符串后，内层循环会把 s[j] 逐个写入 grid[i][j]。`,
          "字符网格题看起来像地图，其实底层仍然是二维数组按坐标写值。",
        ]
      : [
          "先按行读入字符串，再把每个字符映射到对应格子。",
          "按空格后会从左上角开始，逐格完成字符写入。",
        ],
    debug: [
      { label: "row", value: current ? String(current.row) : "-" },
      { label: "col", value: current ? String(current.col) : "-" },
      { label: "currentChar", value: current ? current.value : "-" },
      { label: "targetCell", value: current ? current.label : "尚未开始" },
    ],
    code: {
      lines: [
        "for (int i = 0; i < n; i++) {",
        "  cin >> s;",
        "  for (int j = 0; j < m; j++) {",
        "    grid[i][j] = s[j];",
        "  }",
        "}",
      ],
      activeLines: current ? (current.col === 0 ? [0, 1, 2, 3] : [0, 2, 3]) : [0],
    },
    visual: {
      lead: "上面是输入的字符串行，下面是正在形成的字符网格。每一步都把一个字符精确写进一个格子。",
      cards: lectureFourInputRows.map((rowText, rowIndex) => ({
        label: `第 ${rowIndex + 1} 行输入`,
        value: rowText,
        detail:
          current && rowIndex === current.row
            ? `当前读取 s[${current.col}] = '${current.value}'`
            : `共 ${rowText.length} 个字符`,
        state: current ? (rowIndex < current.row ? "filled" : rowIndex === current.row ? "active" : "pending") : "pending",
      })),
      matrix: {
        title: "字符网格",
        rows: matrix.length,
        cols: matrix[0].length,
        caption: current
          ? `当前已把 '${current.value}' 写入 ${current.label}。`
          : "等待开始后会按行优先顺序把字符写入网格。",
        cells: flattenMatrixEntries(matrix).map((entry, index) => {
          let state = "pending";
          let value = "·";
          if (step > 0 && index < step - 1) {
            state = "filled";
            value = entry.value;
          }
          if (current && entry.row === current.row && entry.col === current.col) {
            state = "active";
            value = entry.value;
          }
          return {
            label: entry.label,
            value,
            state,
          };
        }),
      },
      formula: current ? `s[${current.col}] = '${current.value}' -> grid[${current.row}][${current.col}]` : "整行字符串读入后，再用 s[j] 写入 grid[i][j]。",
    },
  };
}

function buildLectureFourNeighborSnapshot(step) {
  const matrix = rowsToCharMatrix(lectureFourDirectionRows);
  const center = { row: 1, col: 1, label: "a[1][1]", value: matrix[1][1] };
  const neighbors = [
    { direction: "上", row: 0, col: 1, expression: "a[i - 1][j]", delta: "(-1, 0)" },
    { direction: "下", row: 2, col: 1, expression: "a[i + 1][j]", delta: "(+1, 0)" },
    { direction: "左", row: 1, col: 0, expression: "a[i][j - 1]", delta: "(0, -1)" },
    { direction: "右", row: 1, col: 2, expression: "a[i][j + 1]", delta: "(0, +1)" },
  ];
  const currentNeighbor = step >= 2 ? neighbors[step - 2] : null;
  const currentNeighborValue = currentNeighbor ? matrix[currentNeighbor.row][currentNeighbor.col] : null;

  return {
    headline:
      step === 0
        ? "准备从中心格出发观察上下左右"
        : step === 1
          ? `先确定中心格 ${center.label} = '${center.value}'`
          : `当前访问${currentNeighbor.direction}邻居：${currentNeighbor.expression}`,
    badge:
      step <= 1
        ? "中心格 / current cell"
        : `${currentNeighbor.direction}方向 · ${currentNeighbor.expression} · 坐标变化 ${currentNeighbor.delta}`,
    explanation:
      step === 0
        ? [
            "字符地图题最常见的动作是：先定位当前格，再看上下左右四个邻居。",
            "按空格后会先高亮中心格，再依次看上、下、左、右。",
          ]
        : step === 1
          ? [
              `当前格固定在 a[1][1]，字符是 '${center.value}'。`,
              "后面四步都是围绕这个中心格去写邻居表达式。",
            ]
          : [
              `当前表达式是 ${currentNeighbor.expression}，表示从中心格出发做坐标变化 ${currentNeighbor.delta}。`,
              `对应到网格上，就是中心格的${currentNeighbor.direction}方格子 ${cellLabel(currentNeighbor.row, currentNeighbor.col)}。`,
              `当前邻居字符是 '${currentNeighborValue}'，中心格和邻居格需要明显区分。`,
            ],
    debug: [
      { label: "i", value: String(center.row) },
      { label: "j", value: String(center.col) },
      { label: "currentCell", value: center.label },
      { label: "neighborExpression", value: currentNeighbor ? currentNeighbor.expression : "-" },
      { label: "neighborCell", value: currentNeighbor ? cellLabel(currentNeighbor.row, currentNeighbor.col) : "-" },
    ],
    code: {
      lines: [
        "int i = 1, j = 1;",
        "char up = a[i - 1][j];",
        "char down = a[i + 1][j];",
        "char left = a[i][j - 1];",
        "char right = a[i][j + 1];",
      ],
      activeLines: step === 0 ? [0] : [Math.max(0, step - 1)],
    },
    visual: {
      lead: "中心格用一套颜色，当前邻居格用另一套颜色。这样学生能直观看出“当前自己”和“正在看的邻居”不是同一个位置。",
      cards: [
        { label: "中心格", value: `${center.label} = '${center.value}'`, state: "center" },
        {
          label: "当前方向",
          value: currentNeighbor ? currentNeighbor.direction : "等待开始",
          detail: currentNeighbor ? `${currentNeighbor.expression} / ${currentNeighbor.delta}` : "先锁定中心格",
          state: currentNeighbor ? "neighbor" : "filled",
        },
      ],
      matrix: {
        title: "上下左右示意网格",
        rows: matrix.length,
        cols: matrix[0].length,
        caption:
          step <= 1
            ? "先看中心格，再看它的四个方向邻居。"
            : `当前高亮的是${currentNeighbor.direction}邻居 ${currentNeighbor.expression}。`,
        cells: flattenMatrixEntries(matrix).map((entry) => {
          let state = "pending";
          if (entry.row === center.row && entry.col === center.col) {
            state = step === 1 ? "center active" : "center";
          }
          if (currentNeighbor && entry.row === currentNeighbor.row && entry.col === currentNeighbor.col) {
            state = "neighbor active";
          }
          return {
            label: entry.label,
            value: entry.value,
            state,
          };
        }),
      },
      formula:
        step <= 1
          ? "中心格固定后，再通过 i / j 的加减去访问邻居。"
          : `${currentNeighbor.expression} -> ${cellLabel(currentNeighbor.row, currentNeighbor.col)} -> '${currentNeighborValue}'`,
    },
  };
}

function buildLectureFourCropSnapshot(step) {
  const sourceMatrix = rowsToCharMatrix(lectureFourCropRows);
  const sourceTop = 1;
  const sourceLeft = 1;
  const targetRows = 2;
  const targetCols = 3;
  const copyOrder = [];

  for (let row = sourceTop; row < sourceTop + targetRows; row++) {
    for (let col = sourceLeft; col < sourceLeft + targetCols; col++) {
      copyOrder.push({
        sourceRow: row,
        sourceCol: col,
        targetRow: row - sourceTop,
        targetCol: col - sourceLeft,
        char: sourceMatrix[row][col],
      });
    }
  }

  const current = step > 0 ? copyOrder[step - 1] : null;

  return {
    headline: current ? `当前复制 '${current.char}'：源 ${cellLabel(current.sourceRow, current.sourceCol)} -> 目标 ${cellLabel(current.targetRow, current.targetCol)}` : "准备从完整画布裁剪出一个 2 × 3 的子区域",
    badge: current
      ? `source ${cellLabel(current.sourceRow, current.sourceCol)} -> target ${cellLabel(current.targetRow, current.targetCol)}`
      : "画布裁剪 / crop by coordinates",
    explanation: current
      ? [
          `当前复制的字符是 '${current.char}'。`,
          `源位置在完整画布的 ${cellLabel(current.sourceRow, current.sourceCol)}，目标位置在结果矩阵的 ${cellLabel(current.targetRow, current.targetCol)}。`,
          "裁剪题的核心不是重新计算字符，而是把目标位置和源位置一一对应起来。",
        ]
      : [
          "先在完整画布上框出裁剪区域，再按行优先顺序把字符复制到结果区。",
          "按空格后会逐格生成右侧的裁剪结果。",
        ],
    debug: [
      { label: "sourceCell", value: current ? cellLabel(current.sourceRow, current.sourceCol) : "-" },
      { label: "targetCell", value: current ? cellLabel(current.targetRow, current.targetCol) : "-" },
      { label: "currentChar", value: current ? current.char : "-" },
    ],
    code: {
      lines: [
        "for (int i = x1; i <= x2; i++) {",
        "  for (int j = y1; j <= y2; j++) {",
        "    crop[i - x1][j - y1] = canvas[i][j];",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2] : [0],
    },
    visual: {
      lead: "左边是完整画布，右边是裁剪结果。每一步都把一个源格子复制到一个目标格子。",
      matrixPanels: [
        {
          title: "完整字符画布",
          rows: sourceMatrix.length,
          cols: sourceMatrix[0].length,
          caption: "蓝色区域是裁剪框，橙色格子是当前正在复制的源位置。",
          cells: flattenMatrixEntries(sourceMatrix).map((entry) => {
            const inCrop =
              entry.row >= sourceTop &&
              entry.row < sourceTop + targetRows &&
              entry.col >= sourceLeft &&
              entry.col < sourceLeft + targetCols;
            let state = inCrop ? "window source" : "pending";
            if (current && entry.row === current.sourceRow && entry.col === current.sourceCol) {
              state = "window source active";
            }
            return {
              label: entry.label,
              value: entry.value,
              state,
            };
          }),
        },
        {
          title: "裁剪结果区",
          rows: targetRows,
          cols: targetCols,
          caption: "绿色表示已经复制完成，橙色表示当前刚写入的目标位置。",
          cells: Array.from({ length: targetRows * targetCols }, (_, index) => {
            const row = Math.floor(index / targetCols);
            const col = index % targetCols;
            const copiedIndex = copyOrder.findIndex((item) => item.targetRow === row && item.targetCol === col);
            const copied = copiedIndex !== -1 && copiedIndex < step;
            const copiedChar = copiedIndex !== -1 ? copyOrder[copiedIndex].char : "·";
            let state = copied ? "target filled" : "pending";
            if (current && row === current.targetRow && col === current.targetCol) {
              state = "target active";
            }
            return {
              label: cellLabel(row, col),
              value: copied ? copiedChar : "·",
              state,
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "源位置", value: cellLabel(current.sourceRow, current.sourceCol), state: "source" },
            { label: "目标位置", value: cellLabel(current.targetRow, current.targetCol), state: "target" },
            { label: "当前字符", value: current.char, state: "active" },
          ]
        : [
            { label: "裁剪框", value: `左上角 ${cellLabel(sourceTop, sourceLeft)}`, detail: "大小 2 × 3", state: "source" },
            { label: "结果区", value: `${targetRows} × ${targetCols}`, detail: "等待逐格生成", state: "target" },
          ],
      formula: current
        ? `crop[${current.targetRow}][${current.targetCol}] = canvas[${current.sourceRow}][${current.sourceCol}] = '${current.char}'`
        : "目标坐标 = 源坐标减去裁剪框左上角坐标。",
    },
  };
}

function buildLectureFiveEightDirectionSnapshot(step) {
  const matrix = rowsToCharMatrix(lectureFiveDirectionRows);
  const center = { row: 1, col: 1, label: cellLabel(1, 1), value: matrix[1][1] };
  const directions = [
    { name: "左上", row: 0, col: 0, expression: "arr[i - 1][j - 1]", delta: "(-1, -1)" },
    { name: "上", row: 0, col: 1, expression: "arr[i - 1][j]", delta: "(-1, 0)" },
    { name: "右上", row: 0, col: 2, expression: "arr[i - 1][j + 1]", delta: "(-1, +1)" },
    { name: "左", row: 1, col: 0, expression: "arr[i][j - 1]", delta: "(0, -1)" },
    { name: "右", row: 1, col: 2, expression: "arr[i][j + 1]", delta: "(0, +1)" },
    { name: "左下", row: 2, col: 0, expression: "arr[i + 1][j - 1]", delta: "(+1, -1)" },
    { name: "下", row: 2, col: 1, expression: "arr[i + 1][j]", delta: "(+1, 0)" },
    { name: "右下", row: 2, col: 2, expression: "arr[i + 1][j + 1]", delta: "(+1, +1)" },
  ];
  const currentDirection = step >= 2 ? directions[step - 2] : null;
  const visitedDirections = step >= 2 ? directions.slice(0, Math.max(0, step - 2)) : [];
  const currentNeighborValue = currentDirection ? matrix[currentDirection.row][currentDirection.col] : null;

  return {
    headline:
      step === 0
        ? "准备从中心格出发观察八方向邻域"
        : step === 1
          ? `先固定中心格 ${center.label} = '${center.value}'`
          : `当前访问${currentDirection.name}方向：${currentDirection.expression}`,
    badge:
      step <= 1
        ? "八方向 / center first"
        : `${currentDirection.name} · ${currentDirection.expression} · 坐标变化 ${currentDirection.delta}`,
    explanation:
      step === 0
        ? [
            "八方向题的起点和上下左右一样，先锁定中心格，再逐个看周围的邻居。",
            "按空格后会先高亮中心格，再依次访问八个方向。",
          ]
        : step === 1
          ? [
              `中心格固定在 arr[1][1]，字符是 '${center.value}'。`,
              "后续每一步都只是改变相对坐标，不改变中心格本身。",
            ]
          : [
              `当前表达式是 ${currentDirection.expression}，表示从中心格做坐标变化 ${currentDirection.delta}。`,
              `对应到网格上，就是${currentDirection.name}方向的 ${cellLabel(currentDirection.row, currentDirection.col)}。`,
              `中心格和当前方向格必须分开看：中心格是参照系，方向格才是当前被检查的邻居。`,
            ],
    debug: [
      { label: "i", value: String(center.row) },
      { label: "j", value: String(center.col) },
      { label: "currentCell", value: center.label },
      { label: "directionName", value: currentDirection ? currentDirection.name : "-" },
      { label: "neighborExpression", value: currentDirection ? currentDirection.expression : "-" },
      { label: "neighborCell", value: currentDirection ? cellLabel(currentDirection.row, currentDirection.col) : "-" },
    ],
    code: {
      lines: [
        "int dx[8] = {-1, -1, -1, 0, 0, 1, 1, 1};",
        "int dy[8] = {-1, 0, 1, -1, 1, -1, 0, 1};",
        "string dir[8] = {\"左上\", \"上\", \"右上\", \"左\", \"右\", \"左下\", \"下\", \"右下\"};",
        "for (int k = 0; k < 8; k++) {",
        "  int nx = i + dx[k];",
        "  int ny = j + dy[k];",
        "  // arr[nx][ny] 就是当前方向邻居",
        "}",
      ],
      activeLines: step === 0 ? [0, 1, 2] : step === 1 ? [3] : [3, 4, 5, 6],
    },
    visual: {
      lead: "中心格始终保留自己的角色颜色，当前方向格使用另一套颜色。这样学生能直观看出“参考位置”和“当前邻居”不是一回事。",
      cards: [
        { label: "中心格", value: `${center.label} = '${center.value}'`, state: "center" },
        {
          label: "当前方向",
          value: currentDirection ? currentDirection.name : "等待开始",
          detail: currentDirection ? `${currentDirection.expression} / ${currentDirection.delta}` : "先锁定中心格，再进入八方向",
          state: currentDirection ? "neighbor" : "filled",
        },
      ],
      matrix: {
        title: "八方向示意网格",
        rows: matrix.length,
        cols: matrix[0].length,
        caption:
          step <= 1
            ? "先确定中心格，再逐个访问左上、上、右上、左、右、左下、下、右下。"
            : `当前高亮的是${currentDirection.name}方向邻居 ${currentDirection.expression}。`,
        cells: flattenMatrixEntries(matrix).map((entry) => {
          let state = "pending";
          if (entry.row === center.row && entry.col === center.col) {
            state = step === 1 ? "center active" : "center";
          }
          if (
            visitedDirections.some((item) => item.row === entry.row && item.col === entry.col)
          ) {
            state = "neighbor";
          }
          if (currentDirection && entry.row === currentDirection.row && entry.col === currentDirection.col) {
            state = "neighbor active";
          }
          return {
            label: cellLabel(entry.row, entry.col),
            value: entry.value,
            state,
          };
        }),
      },
      formula:
        step <= 1
          ? "八方向本质上是 8 组不同的 (di, dj) 偏移。"
          : `${currentDirection.expression} -> ${cellLabel(currentDirection.row, currentDirection.col)} -> '${currentNeighborValue}'`,
    },
  };
}

function buildLectureFiveSubrectSnapshot(step) {
  const matrix = rowsToCharMatrix(lectureFiveSubrectRows);
  const windowSize = 3;
  const cases = buildCharWindowCases(matrix, windowSize);
  const current = step > 0 ? cases[step - 1] : null;

  return {
    headline: current ? `当前枚举左上角 ${cellLabel(current.top, current.left)}` : "准备枚举 5 × 5 网格中所有合法的 3 × 3 子矩形左上角",
    badge: current
      ? `leftTop = ${cellLabel(current.top, current.left)} · 范围 ${cellLabel(current.top, current.left)} 到 ${cellLabel(current.bottom, current.right)}`
      : "子矩形枚举 / 左上角驱动",
    explanation: current
      ? [
          `当前子矩形大小固定为 ${windowSize}×${windowSize}，左上角在 ${cellLabel(current.top, current.left)}。`,
          `当前位置合法，因为 bottom = ${current.bottom} <= ${matrix.length - 1}，right = ${current.right} <= ${matrix[0].length - 1}。`,
          `当前窗口覆盖的格子有：${current.entries.map((entry) => cellLabel(entry.row, entry.col)).join("、")}。`,
        ]
      : [
          "子矩形题不是乱滑动，而是系统枚举每一个合法左上角。",
          "这里用 5×5 网格枚举全部 3×3 子矩形，因此一共有 9 个合法位置。",
        ],
    debug: [
      { label: "leftTop", value: current ? cellLabel(current.top, current.left) : "-" },
      { label: "windowSize", value: `${windowSize}x${windowSize}` },
      { label: "currentWindowCells", value: current ? current.entries.map((entry) => cellLabel(entry.row, entry.col)).join(", ") : "尚未开始" },
    ],
    code: {
      lines: [
        "int K = 3;",
        "for (int i = 0; i + K - 1 < rows; i++) {",
        "  for (int j = 0; j + K - 1 < cols; j++) {",
        "    // 左上角是 (i, j)",
        "    // 当前子矩形范围是 [i, i + K - 1] × [j, j + K - 1]",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2, 3, 4] : [0],
    },
    visual: {
      lead: "子矩形枚举的关键是：固定窗口大小以后，只需要移动左上角。只要右下角还没越界，这个位置就是合法的。",
      cards: current
        ? [
            { label: "当前左上角", value: cellLabel(current.top, current.left), state: "active" },
            { label: "当前范围", value: `${cellLabel(current.top, current.left)} -> ${cellLabel(current.bottom, current.right)}`, state: "source" },
            { label: "合法原因", value: `bottom=${current.bottom}, right=${current.right}`, detail: "仍在网格边界内", state: "success" },
          ]
        : [
            { label: "网格大小", value: "5 × 5", state: "filled" },
            { label: "窗口大小", value: "3 × 3", state: "filled" },
            { label: "合法位置数", value: String(cases.length), state: "success" },
          ],
      matrix: {
        title: "3 × 3 子矩形左上角枚举",
        rows: matrix.length,
        cols: matrix[0].length,
        caption: current
          ? `当前高亮的是以 ${cellLabel(current.top, current.left)} 为左上角的 3 × 3 子矩形。`
          : "等待开始后会按行优先顺序枚举全部合法左上角。",
        cells: flattenMatrixEntries(matrix).map((entry) => {
          if (!current) {
            return {
              label: cellLabel(entry.row, entry.col),
              value: entry.value,
              state: "pending",
            };
          }

          const inWindow = current.entries.some((item) => item.row === entry.row && item.col === entry.col);
          const isAnchor = entry.row === current.top && entry.col === current.left;
          return {
            label: cellLabel(entry.row, entry.col),
            value: entry.value,
            state: isAnchor ? "window active" : inWindow ? "window" : "pending",
          };
        }),
      },
      formula:
        current
          ? `i + K - 1 = ${current.bottom} <= ${matrix.length - 1}，j + K - 1 = ${current.right} <= ${matrix[0].length - 1}`
          : "合法条件：i + K - 1 < rows 且 j + K - 1 < cols。",
    },
  };
}

function buildLectureFiveCheckSnapshot(step) {
  const actualMatrix = rowsToCharMatrix(lectureFiveCheckRows);
  const patternMatrix = rowsToCharMatrix(lectureFiveCheckPatternRows);
  const leftTop = { row: 1, col: 1 };
  const checks = [];

  for (let row = 0; row < patternMatrix.length; row++) {
    for (let col = 0; col < patternMatrix[0].length; col++) {
      checks.push({
        offsetRow: row,
        offsetCol: col,
        actualRow: leftTop.row + row,
        actualCol: leftTop.col + col,
        expectedValue: patternMatrix[row][col],
        actualValue: actualMatrix[leftTop.row + row][leftTop.col + col],
      });
    }
  }

  const current = step > 0 ? checks[step - 1] : null;
  const matched = current ? current.expectedValue === current.actualValue : null;
  const previousPassed = checks.slice(0, Math.max(0, step - 1)).filter((item) => item.expectedValue === item.actualValue);
  const checkStatus = current ? (matched ? (step === checks.length ? "通过，继续到结束" : "通过，继续检查") : "失败") : "等待开始";

  return {
    headline: current
      ? `当前检查 ${cellLabel(current.actualRow, current.actualCol)}：期望 '${current.expectedValue}'，实际 '${current.actualValue}'`
      : "准备从固定左上角出发，逐格执行 check(x, y)",
    badge: current
      ? `leftTop = ${cellLabel(leftTop.row, leftTop.col)} · checkStatus = ${checkStatus}`
      : "局部判断 / check(x, y)",
    explanation: current
      ? [
          `当前检查格子是 ${cellLabel(current.actualRow, current.actualCol)}。`,
          matched
            ? `期望值 '${current.expectedValue}' 与实际值 '${current.actualValue}' 一致，因此这一格通过，继续检查下一格。`
            : `期望值 '${current.expectedValue}' 与实际值 '${current.actualValue}' 不一致，因此在这一步失败，失败位置就是 ${cellLabel(current.actualRow, current.actualCol)}。`,
          "局部判断函数的本质是逐格对照，一旦失败就可以直接返回 false。",
        ]
      : [
          "这一组用一个 2×2 图案匹配示意 check(x, y) 的执行过程。",
          "按空格后会逐格比对实际值和期望值，并明确显示在哪一步失败。",
        ],
    debug: [
      { label: "leftTop", value: cellLabel(leftTop.row, leftTop.col) },
      { label: "checkingCell", value: current ? cellLabel(current.actualRow, current.actualCol) : "-" },
      { label: "expectedValue", value: current ? current.expectedValue : "-" },
      { label: "actualValue", value: current ? current.actualValue : "-" },
      { label: "checkStatus", value: checkStatus },
    ],
    code: {
      lines: [
        "bool check(int x, int y) {",
        "  for (int i = 0; i < 2; i++) {",
        "    for (int j = 0; j < 2; j++) {",
        "      if (a[x + i][y + j] != pattern[i][j]) {",
        "        return false;",
        "      }",
        "    }",
        "  }",
        "  return true;",
        "}",
      ],
      activeLines: current ? (matched ? [1, 2, 3] : [1, 2, 3, 4]) : [0],
    },
    visual: {
      lead: "左边是实际网格，右边是期望图案。当前检查格会同步在两边高亮，失败时直接用红色标出问题位置。",
      matrixPanels: [
        {
          title: "实际网格",
          rows: actualMatrix.length,
          cols: actualMatrix[0].length,
          caption: "绿色表示已通过检查，红色表示当前失败位置。",
          cells: flattenMatrixEntries(actualMatrix).map((entry) => {
            let state = "pending";
            if (previousPassed.some((item) => item.actualRow === entry.row && item.actualCol === entry.col)) {
              state = "filled";
            }
            if (current && entry.row === current.actualRow && entry.col === current.actualCol) {
              state = matched ? "active" : "fail active";
            }
            return {
              label: cellLabel(entry.row, entry.col),
              value: entry.value,
              state,
            };
          }),
        },
        {
          title: "期望图案",
          rows: patternMatrix.length,
          cols: patternMatrix[0].length,
          caption: "右侧图案表示 check() 期望看到的局部结构。",
          cells: flattenMatrixEntries(patternMatrix).map((entry) => {
            let state = "pending";
            if (previousPassed.some((item) => item.offsetRow === entry.row && item.offsetCol === entry.col)) {
              state = "filled";
            }
            if (current && entry.row === current.offsetRow && entry.col === current.offsetCol) {
              state = matched ? "active" : "fail active";
            }
            return {
              label: cellLabel(entry.row, entry.col),
              value: entry.value,
              state,
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "当前比较", value: `'${current.actualValue}' vs '${current.expectedValue}'`, state: matched ? "success" : "fail" },
            { label: "当前位置", value: cellLabel(current.actualRow, current.actualCol), state: matched ? "filled" : "fail" },
            { label: "状态", value: checkStatus, detail: matched ? "这一格通过，继续检查" : "在这一格失败，check 返回 false", state: matched ? "success" : "fail" },
          ]
        : [
            { label: "起点 leftTop", value: cellLabel(leftTop.row, leftTop.col), state: "filled" },
            { label: "图案大小", value: "2 × 2", state: "filled" },
          ],
      formula:
        current
          ? `a[${current.actualRow}][${current.actualCol}] ${matched ? "==" : "!="} pattern[${current.offsetRow}][${current.offsetCol}]`
          : "逐格比较：a[x + i][y + j] 是否等于 pattern[i][j]。",
    },
  };
}

function buildLectureSixHorizontalFlipSnapshot(step) {
  const baseMatrix = lectureSixHorizontalMatrix;
  const steps = buildHorizontalFlipSteps(baseMatrix);
  const current = step > 0 ? steps[step - 1] : null;
  const beforeMatrix = applyCellSwapSteps(baseMatrix, steps, Math.max(0, step - 1));
  const afterMatrix = applyCellSwapSteps(baseMatrix, steps, step);
  const previousKeys = collectSwapPositionKeys(steps, Math.max(0, step - 1));

  const beforeLeft = current ? beforeMatrix[current.r1][current.c1] : null;
  const beforeRight = current ? beforeMatrix[current.r2][current.c2] : null;
  const afterLeft = current ? afterMatrix[current.r1][current.c1] : null;
  const afterRight = current ? afterMatrix[current.r2][current.c2] : null;

  return {
    headline: current ? `当前处理第 ${current.row + 1} 行：交换 ${cellLabel(current.r1, current.c1)} 和 ${cellLabel(current.r2, current.c2)}` : "准备开始按行做左右翻转",
    badge: current ? `row = ${current.row} · ${beforeLeft}<->${beforeRight} -> ${afterLeft}<->${afterRight}` : "左右翻转 / per-row swap",
    explanation: current
      ? [
          `当前正在处理第 ${current.row + 1} 行。`,
          `本步交换的是 ${cellLabel(current.r1, current.c1)} 和 ${cellLabel(current.r2, current.c2)}。`,
          `交换前是 ${beforeLeft} 和 ${beforeRight}，交换后变成 ${afterLeft} 和 ${afterRight}。`,
        ]
      : [
          "左右翻转不是整行瞬移，而是每一行内部做若干次左右对称交换。",
          "按空格后会依次走过所有需要交换的位置对。",
        ],
    debug: [
      { label: "row", value: current ? String(current.row) : "-" },
      { label: "leftCol", value: current ? String(current.leftCol) : "-" },
      { label: "rightCol", value: current ? String(current.rightCol) : "-" },
      { label: "leftCell", value: current ? cellLabel(current.r1, current.c1) : "-" },
      { label: "rightCell", value: current ? cellLabel(current.r2, current.c2) : "-" },
      { label: "currentAction", value: current ? `swap ${cellLabel(current.r1, current.c1)} <-> ${cellLabel(current.r2, current.c2)}` : "等待开始" },
    ],
    code: {
      lines: [
        "for (int row = 0; row < rows; row++) {",
        "  for (int left = 0, right = cols - 1; left < right; left++, right--) {",
        "    swap(a[row][left], a[row][right]);",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2] : [0],
    },
    visual: {
      lead: "左侧保留原矩阵，右侧显示当前翻转进度。这样学生能看到“交换进行到哪一步”，而不是只看到最终答案。",
      matrixPanels: [
        {
          title: "原矩阵",
          rows: baseMatrix.length,
          cols: baseMatrix[0].length,
          caption: "原矩阵保持不变，只用于对照当前步骤。",
          cells: flattenMatrixEntries(baseMatrix).map((entry) => ({
            label: cellLabel(entry.row, entry.col),
            value: String(entry.value),
            state:
              current && entry.row === current.r1 && entry.col === current.c1
                ? "source active"
                : current && entry.row === current.r2 && entry.col === current.c2
                  ? "target active"
                  : "pending",
          })),
        },
        {
          title: "当前翻转进度",
          rows: afterMatrix.length,
          cols: afterMatrix[0].length,
          caption: current ? `第 ${current.row + 1} 行正在进行左右对称交换。` : "等待开始后会逐行完成翻转。",
          cells: flattenMatrixEntries(afterMatrix).map((entry) => {
            const key = positionKey(entry.row, entry.col);
            let state = "pending";
            if (current && entry.row === current.row) {
              state = "row-active";
            }
            if (previousKeys.has(key)) {
              state = "filled";
            }
            if (current && entry.row === current.r1 && entry.col === current.c1) {
              state = "source active";
            }
            if (current && entry.row === current.r2 && entry.col === current.c2) {
              state = "target active";
            }
            return {
              label: cellLabel(entry.row, entry.col),
              value: String(entry.value),
              state,
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "当前行", value: `row ${current.row}`, state: "filled" },
            { label: "交换前", value: `${beforeLeft} / ${beforeRight}`, state: "source" },
            { label: "交换后", value: `${afterLeft} / ${afterRight}`, state: "success" },
          ]
        : [
            { label: "处理方式", value: "左右对称 swap", detail: "每行分别处理", state: "filled" },
          ],
      formula: current ? `swap(a[${current.row}][${current.leftCol}], a[${current.row}][${current.rightCol}])` : "每行都让 left 向右走、right 向左走，直到 left >= right。",
    },
  };
}

function buildLectureSixVerticalFlipSnapshot(step) {
  const baseMatrix = lectureSixVerticalMatrix;
  const steps = buildVerticalFlipSteps(baseMatrix);
  const current = step > 0 ? steps[step - 1] : null;
  const beforeMatrix = applyCellSwapSteps(baseMatrix, steps, Math.max(0, step - 1));
  const afterMatrix = applyCellSwapSteps(baseMatrix, steps, step);
  const previousKeys = collectSwapPositionKeys(steps, Math.max(0, step - 1));

  const beforeTop = current ? beforeMatrix[current.r1][current.c1] : null;
  const beforeBottom = current ? beforeMatrix[current.r2][current.c2] : null;
  const afterTop = current ? afterMatrix[current.r1][current.c1] : null;
  const afterBottom = current ? afterMatrix[current.r2][current.c2] : null;

  return {
    headline: current ? `当前交换第 ${current.topRow + 1} 行和第 ${current.bottomRow + 1} 行的第 ${current.col + 1} 列` : "准备开始做上下翻转",
    badge: current ? `topRow=${current.topRow} · bottomRow=${current.bottomRow} · col=${current.col}` : "上下翻转 / swap row pairs",
    explanation: current
      ? [
          `当前交换的是第 ${current.topRow + 1} 行和第 ${current.bottomRow + 1} 行。`,
          `本步处理到第 ${current.col + 1} 列，对应位置是 ${cellLabel(current.r1, current.c1)} 和 ${cellLabel(current.r2, current.c2)}。`,
          `交换前是 ${beforeTop} / ${beforeBottom}，交换后变成 ${afterTop} / ${afterBottom}。`,
        ]
      : [
          "上下翻转不是整行瞬移，而是先配对上下两行，再逐列交换。",
          "按空格后会按列推进，把一对行完整交换完再继续下一对。",
        ],
    debug: [
      { label: "topRow", value: current ? String(current.topRow) : "-" },
      { label: "bottomRow", value: current ? String(current.bottomRow) : "-" },
      { label: "col", value: current ? String(current.col) : "-" },
      { label: "sourceCell", value: current ? cellLabel(current.r1, current.c1) : "-" },
      { label: "targetCell", value: current ? cellLabel(current.r2, current.c2) : "-" },
      { label: "currentAction", value: current ? `swap ${cellLabel(current.r1, current.c1)} <-> ${cellLabel(current.r2, current.c2)}` : "等待开始" },
    ],
    code: {
      lines: [
        "for (int top = 0, bottom = rows - 1; top < bottom; top++, bottom--) {",
        "  for (int col = 0; col < cols; col++) {",
        "    swap(a[top][col], a[bottom][col]);",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2] : [0],
    },
    visual: {
      lead: "左边保留原矩阵，右边显示当前上下翻转进度。当前处理的两行会一直保留角色颜色，当前交换格单独高亮。",
      matrixPanels: [
        {
          title: "原矩阵",
          rows: baseMatrix.length,
          cols: baseMatrix[0].length,
          caption: "原矩阵用于对照本步交换来源。",
          cells: flattenMatrixEntries(baseMatrix).map((entry) => ({
            label: cellLabel(entry.row, entry.col),
            value: String(entry.value),
            state:
              current && entry.row === current.r1 && entry.col === current.c1
                ? "source active"
                : current && entry.row === current.r2 && entry.col === current.c2
                  ? "target active"
                  : "pending",
          })),
        },
        {
          title: "当前翻转进度",
          rows: afterMatrix.length,
          cols: afterMatrix[0].length,
          caption: current ? `当前处理上下行对：row ${current.topRow} 和 row ${current.bottomRow}。` : "等待开始后会逐列交换上下两行。",
          cells: flattenMatrixEntries(afterMatrix).map((entry) => {
            const key = positionKey(entry.row, entry.col);
            let state = "pending";
            if (current && entry.row === current.topRow) {
              state = "source";
            }
            if (current && entry.row === current.bottomRow) {
              state = "target";
            }
            if (previousKeys.has(key)) {
              state = "filled";
            }
            if (current && entry.row === current.r1 && entry.col === current.c1) {
              state = "source active";
            }
            if (current && entry.row === current.r2 && entry.col === current.c2) {
              state = "target active";
            }
            return {
              label: cellLabel(entry.row, entry.col),
              value: String(entry.value),
              state,
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "行对", value: `${current.topRow} <-> ${current.bottomRow}`, state: "filled" },
            { label: "交换前", value: `${beforeTop} / ${beforeBottom}`, state: "source" },
            { label: "交换后", value: `${afterTop} / ${afterBottom}`, state: "success" },
          ]
        : [
            { label: "处理方式", value: "上下两行配对后逐列交换", state: "filled" },
          ],
      formula: current ? `swap(a[${current.topRow}][${current.col}], a[${current.bottomRow}][${current.col}])` : "top 向下走、bottom 向上走，每次交换整对行中的同列元素。",
    },
  };
}

function buildLectureSixTransposeSnapshot(step) {
  const sourceMatrix = lectureSixTransposeMatrix;
  const order = flattenMatrixEntries(sourceMatrix);
  const current = step > 0 ? order[step - 1] : null;
  const targetMatrix = createPlaceholderMatrix(sourceMatrix[0].length, sourceMatrix.length, "·");

  order.slice(0, step).forEach((entry) => {
    targetMatrix[entry.col][entry.row] = entry.value;
  });

  return {
    headline: current ? `当前把 ${current.label} = ${current.value} 放到 b[${current.col}][${current.row}]` : "准备开始做“原矩阵 -> 目标矩阵”的转置",
    badge: current ? `${current.label} -> b[${current.col}][${current.row}]` : "转置 / source to target",
    explanation: current
      ? [
          `当前取的是源矩阵里的 ${current.label}。`,
          `转置后它要放到目标矩阵的 b[${current.col}][${current.row}]，也就是行列互换。`,
          `这一版采用“原矩阵到目标矩阵”的方式，更适合教学展示映射关系。`,
        ]
      : [
          "转置最适合同时看两张矩阵：左边是源矩阵，右边是目标矩阵。",
          "按空格后会逐格把 a[i][j] 复制到 b[j][i]。",
        ],
    debug: [
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "sourceCell", value: current ? current.label : "-" },
      { label: "targetCell", value: current ? `b[${current.col}][${current.row}]` : "-" },
      { label: "currentValue", value: current ? String(current.value) : "-" },
    ],
    code: {
      lines: [
        "for (int i = 0; i < n; i++) {",
        "  for (int j = 0; j < n; j++) {",
        "    b[j][i] = a[i][j];",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2] : [0],
    },
    visual: {
      lead: "转置最直观的教学方式，是让学生同时看到“从哪来”与“放到哪去”。",
      matrixPanels: [
        {
          title: "源矩阵 a",
          rows: sourceMatrix.length,
          cols: sourceMatrix[0].length,
          caption: "左侧表示当前正在读取的源位置。",
          cells: flattenMatrixEntries(sourceMatrix).map((entry, index) => ({
            label: cellLabel(entry.row, entry.col),
            value: String(entry.value),
            state:
              current && entry.row === current.row && entry.col === current.col
                ? "source active"
                : step > 0 && index < step - 1
                  ? "filled"
                  : "pending",
          })),
        },
        {
          title: "目标矩阵 b",
          rows: targetMatrix.length,
          cols: targetMatrix[0].length,
          caption: "右侧表示当前已经生成到哪一步。",
          cells: flattenMatrixEntries(targetMatrix).map((entry) => {
            const mapped = current && entry.row === current.col && entry.col === current.row;
            const filled = entry.value !== "·";
            return {
              label: `b[${entry.row}][${entry.col}]`,
              value: String(entry.value),
              state: mapped ? "target active" : filled ? "filled" : "pending",
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "源位置", value: current.label, state: "source" },
            { label: "目标位置", value: `b[${current.col}][${current.row}]`, state: "target" },
            { label: "当前值", value: String(current.value), state: "active" },
          ]
        : [
            { label: "转置规则", value: "行列互换", detail: "a[i][j] -> b[j][i]", state: "filled" },
          ],
      formula: current ? `b[${current.col}][${current.row}] = a[${current.row}][${current.col}] = ${current.value}` : "转置就是把行索引和列索引互换。",
    },
  };
}

function buildLectureSixRowSwapSnapshot(step) {
  const baseMatrix = lectureSixRowSwapMatrix;
  const rowA = 0;
  const rowB = 2;
  const steps = buildRowSwapSteps(baseMatrix, rowA, rowB);
  const current = step > 0 ? steps[step - 1] : null;
  const beforeMatrix = applyCellSwapSteps(baseMatrix, steps, Math.max(0, step - 1));
  const afterMatrix = applyCellSwapSteps(baseMatrix, steps, step);
  const previousKeys = collectSwapPositionKeys(steps, Math.max(0, step - 1));

  const beforeA = current ? beforeMatrix[current.r1][current.c1] : null;
  const beforeB = current ? beforeMatrix[current.r2][current.c2] : null;
  const afterA = current ? afterMatrix[current.r1][current.c1] : null;
  const afterB = current ? afterMatrix[current.r2][current.c2] : null;

  return {
    headline: current ? `当前交换第 ${rowA + 1} 行和第 ${rowB + 1} 行的第 ${current.col + 1} 列` : "准备开始按列完成整行交换",
    badge: current ? `rowA=${rowA} · rowB=${rowB} · col=${current.col}` : "行交换 / swap whole row gradually",
    explanation: current
      ? [
          `整行交换并不是一瞬间完成，而是当前这样按列逐步交换。`,
          `本步处理到第 ${current.col + 1} 列，对应位置是 ${cellLabel(current.r1, current.c1)} 和 ${cellLabel(current.r2, current.c2)}。`,
          `交换前是 ${beforeA} / ${beforeB}，交换后是 ${afterA} / ${afterB}。`,
        ]
      : [
          "本模块固定交换第 1 行和第 3 行，用最直接的方式展示整行交换的过程。",
          "按空格后会沿着列推进，直到整行完成。",
        ],
    debug: [
      { label: "rowA", value: String(rowA) },
      { label: "rowB", value: String(rowB) },
      { label: "col", value: current ? String(current.col) : "-" },
      { label: "cellA", value: current ? cellLabel(current.r1, current.c1) : "-" },
      { label: "cellB", value: current ? cellLabel(current.r2, current.c2) : "-" },
    ],
    code: {
      lines: [
        "int rowA = 0, rowB = 2;",
        "for (int col = 0; col < cols; col++) {",
        "  swap(a[rowA][col], a[rowB][col]);",
        "}",
      ],
      activeLines: current ? [0, 1, 2] : [0],
    },
    visual: {
      lead: "左边保留原矩阵，右边显示当前行交换进度。目标是让学生看到“一整行交换”其实就是很多个单元交换的串联。",
      matrixPanels: [
        {
          title: "原矩阵",
          rows: baseMatrix.length,
          cols: baseMatrix[0].length,
          caption: "原矩阵只做对照，帮助理解来源。",
          cells: flattenMatrixEntries(baseMatrix).map((entry) => ({
            label: cellLabel(entry.row, entry.col),
            value: String(entry.value),
            state:
              current && entry.row === current.r1 && entry.col === current.c1
                ? "source active"
                : current && entry.row === current.r2 && entry.col === current.c2
                  ? "target active"
                  : entry.row === rowA
                    ? "source"
                    : entry.row === rowB
                      ? "target"
                      : "pending",
          })),
        },
        {
          title: "当前交换进度",
          rows: afterMatrix.length,
          cols: afterMatrix[0].length,
          caption: current ? `当前沿着第 ${current.col + 1} 列推进整行交换。` : "等待开始后会逐列完成整行交换。",
          cells: flattenMatrixEntries(afterMatrix).map((entry) => {
            const key = positionKey(entry.row, entry.col);
            let state = "pending";
            if (entry.row === rowA) {
              state = "source";
            }
            if (entry.row === rowB) {
              state = "target";
            }
            if (previousKeys.has(key)) {
              state = "filled";
            }
            if (current && entry.row === current.r1 && entry.col === current.c1) {
              state = "source active";
            }
            if (current && entry.row === current.r2 && entry.col === current.c2) {
              state = "target active";
            }
            return {
              label: cellLabel(entry.row, entry.col),
              value: String(entry.value),
              state,
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "交换行", value: `${rowA} <-> ${rowB}`, state: "filled" },
            { label: "交换前", value: `${beforeA} / ${beforeB}`, state: "source" },
            { label: "交换后", value: `${afterA} / ${afterB}`, state: "success" },
          ]
        : [
            { label: "核心概念", value: "整行交换 = 按列逐格交换", state: "filled" },
          ],
      formula: current ? `swap(a[${rowA}][${current.col}], a[${rowB}][${current.col}])` : "选中两行后，按 col 从左到右逐列交换。",
    },
  };
}

function buildLectureSixColSwapSnapshot(step) {
  const baseMatrix = lectureSixColSwapMatrix;
  const colA = 0;
  const colB = 3;
  const steps = buildColSwapSteps(baseMatrix, colA, colB);
  const current = step > 0 ? steps[step - 1] : null;
  const beforeMatrix = applyCellSwapSteps(baseMatrix, steps, Math.max(0, step - 1));
  const afterMatrix = applyCellSwapSteps(baseMatrix, steps, step);
  const previousKeys = collectSwapPositionKeys(steps, Math.max(0, step - 1));

  const beforeA = current ? beforeMatrix[current.r1][current.c1] : null;
  const beforeB = current ? beforeMatrix[current.r2][current.c2] : null;
  const afterA = current ? afterMatrix[current.r1][current.c1] : null;
  const afterB = current ? afterMatrix[current.r2][current.c2] : null;

  return {
    headline: current ? `当前交换第 ${colA + 1} 列和第 ${colB + 1} 列的第 ${current.row + 1} 行` : "准备开始按行完成整列交换",
    badge: current ? `colA=${colA} · colB=${colB} · row=${current.row}` : "列交换 / swap whole column gradually",
    explanation: current
      ? [
          `整列交换并不是一瞬间完成，而是当前这样按行逐步交换。`,
          `本步处理到第 ${current.row + 1} 行，对应位置是 ${cellLabel(current.r1, current.c1)} 和 ${cellLabel(current.r2, current.c2)}。`,
          `交换前是 ${beforeA} / ${beforeB}，交换后是 ${afterA} / ${afterB}。`,
        ]
      : [
          "本模块固定交换第 1 列和第 4 列，用逐行推进的方式展示整列交换。",
          "按空格后会顺着每一行推进，直到整列完成。",
        ],
    debug: [
      { label: "colA", value: String(colA) },
      { label: "colB", value: String(colB) },
      { label: "row", value: current ? String(current.row) : "-" },
      { label: "cellA", value: current ? cellLabel(current.r1, current.c1) : "-" },
      { label: "cellB", value: current ? cellLabel(current.r2, current.c2) : "-" },
    ],
    code: {
      lines: [
        "int colA = 0, colB = 3;",
        "for (int row = 0; row < rows; row++) {",
        "  swap(a[row][colA], a[row][colB]);",
        "}",
      ],
      activeLines: current ? [0, 1, 2] : [0],
    },
    visual: {
      lead: "左边保留原矩阵，右边显示当前列交换进度。两列会一直保留角色颜色，当前交换单元额外高亮。",
      matrixPanels: [
        {
          title: "原矩阵",
          rows: baseMatrix.length,
          cols: baseMatrix[0].length,
          caption: "原矩阵用于说明当前两列的来源。",
          cells: flattenMatrixEntries(baseMatrix).map((entry) => ({
            label: cellLabel(entry.row, entry.col),
            value: String(entry.value),
            state:
              current && entry.row === current.r1 && entry.col === current.c1
                ? "source active"
                : current && entry.row === current.r2 && entry.col === current.c2
                  ? "target active"
                  : entry.col === colA
                    ? "source"
                    : entry.col === colB
                      ? "target"
                      : "pending",
          })),
        },
        {
          title: "当前交换进度",
          rows: afterMatrix.length,
          cols: afterMatrix[0].length,
          caption: current ? `当前沿着第 ${current.row + 1} 行推进整列交换。` : "等待开始后会逐行完成整列交换。",
          cells: flattenMatrixEntries(afterMatrix).map((entry) => {
            const key = positionKey(entry.row, entry.col);
            let state = "pending";
            if (entry.col === colA) {
              state = "source";
            }
            if (entry.col === colB) {
              state = "target";
            }
            if (previousKeys.has(key)) {
              state = "filled";
            }
            if (current && entry.row === current.r1 && entry.col === current.c1) {
              state = "source active";
            }
            if (current && entry.row === current.r2 && entry.col === current.c2) {
              state = "target active";
            }
            return {
              label: cellLabel(entry.row, entry.col),
              value: String(entry.value),
              state,
            };
          }),
        },
      ],
      cards: current
        ? [
            { label: "交换列", value: `${colA} <-> ${colB}`, state: "filled" },
            { label: "交换前", value: `${beforeA} / ${beforeB}`, state: "source" },
            { label: "交换后", value: `${afterA} / ${afterB}`, state: "success" },
          ]
        : [
            { label: "核心概念", value: "整列交换 = 按行逐格交换", state: "filled" },
          ],
      formula: current ? `swap(a[${current.row}][${colA}], a[${current.row}][${colB}])` : "选中两列后，按 row 从上到下逐行交换。",
    },
  };
}

function buildLectureSixLinearMapSnapshot(step) {
  const matrix = lectureSixLinearMatrix;
  const cols = matrix[0].length;
  const order = flattenMatrixEntries(matrix);
  const current = step > 0 ? order[step - 1] : null;
  const linearIndex = current ? current.row * cols + current.col : null;

  return {
    headline: current ? `当前映射 ${current.label} -> flat[${linearIndex}]` : "准备把二维矩阵按行展开成一维线性数组",
    badge: current ? `linearIndex = ${current.row} * ${cols} + ${current.col} = ${linearIndex}` : "按行展开 / row-major mapping",
    explanation: current
      ? [
          `当前二维位置是 ${current.label}。`,
          `按行展开后，它在线性数组中的位置是 flat[${linearIndex}]。`,
          "这就是为什么二维数组在内存里看起来像一条连续的一维空间，而访问时仍然能写成 a[i][j]。",
        ]
      : [
          "这一组是整套专题的收口：把二维矩阵和一维线性数组放在一起解释按行展开。",
          "按空格后会同步高亮二维中的 a[i][j] 和一维中的 flat[index]。",
        ],
    debug: [
      { label: "i", value: current ? String(current.row) : "-" },
      { label: "j", value: current ? String(current.col) : "-" },
      { label: "linearIndex", value: linearIndex === null ? "-" : String(linearIndex) },
      { label: "currentCell", value: current ? current.label : "-" },
      { label: "mappedCell", value: linearIndex === null ? "-" : `flat[${linearIndex}]` },
    ],
    code: {
      lines: [
        "for (int i = 0; i < rows; i++) {",
        "  for (int j = 0; j < cols; j++) {",
        "    int index = i * cols + j;",
        "    flat[index] = a[i][j];",
        "  }",
        "}",
      ],
      activeLines: current ? [0, 1, 2, 3] : [0],
    },
    visual: {
      lead: "二维矩阵只是更方便的坐标视图，底层仍然是一条按行展开的线性数组。",
      matrix: {
        title: "二维矩阵",
        rows: matrix.length,
        cols: matrix[0].length,
        caption: current ? `当前高亮 ${current.label}，它会映射到 flat[${linearIndex}]。` : "等待开始后会按行依次展开全部元素。",
        cells: flattenMatrixEntries(matrix).map((entry, index) => ({
          label: cellLabel(entry.row, entry.col),
          value: String(entry.value),
          state:
            current && entry.row === current.row && entry.col === current.col
              ? "active"
              : step > 0 && index < step - 1
                ? "filled"
                : "pending",
        })),
      },
      memory: {
        title: "一维线性数组 flat[]",
        caption: "线性下标只会连续增加，二维坐标只是它的解释方式。",
        cells: flattenMatrixEntries(matrix).map((entry, index) => ({
          label: `flat[${index}]`,
          detail: String(entry.value),
          state: current && index === linearIndex ? "active" : step > 0 && index < step - 1 ? "filled" : "pending",
        })),
      },
      cards: current
        ? [
            { label: "二维位置", value: current.label, state: "active" },
            { label: "线性位置", value: `flat[${linearIndex}]`, state: "target" },
            { label: "当前值", value: String(current.value), state: "success" },
          ]
        : [
            { label: "映射规则", value: "index = i * cols + j", state: "filled" },
          ],
      formula: current ? `flat[${linearIndex}] = a[${current.row}][${current.col}] = ${current.value}` : "每一行都顺着铺到一维数组里，然后继续下一行。",
    },
  };
}

function flattenMatrixEntries(matrix) {
  return matrix.flatMap((row, rowIndex) =>
    row.map((value, colIndex) => ({
      row: rowIndex,
      col: colIndex,
      value,
      label: `a[${rowIndex}][${colIndex}]`,
    })),
  );
}

function sumNumberList(numbers) {
  return numbers.reduce((sum, value) => sum + value, 0);
}

function buildVisitedMatrixCells(matrix, step, current) {
  const order = flattenMatrixEntries(matrix);
  return order.map((entry, index) => {
    let state = "pending";
    if (step > 0 && index < step - 1) {
      state = "filled";
    }
    if (current && entry.row === current.row && entry.col === current.col) {
      state = "active";
    }
    return {
      label: entry.label,
      value: String(entry.value),
      state,
    };
  });
}

function cloneMatrix(matrix) {
  return matrix.map((row) => row.slice());
}

function createPlaceholderMatrix(rows, cols, placeholder) {
  return Array.from({ length: rows }, () => Array.from({ length: cols }, () => placeholder));
}

function positionKey(row, col) {
  return `${row}:${col}`;
}

function collectSwapPositionKeys(steps, count) {
  const keys = new Set();
  steps.slice(0, count).forEach((step) => {
    keys.add(positionKey(step.r1, step.c1));
    keys.add(positionKey(step.r2, step.c2));
  });
  return keys;
}

function applyCellSwapSteps(matrix, steps, count) {
  const next = cloneMatrix(matrix);
  steps.slice(0, count).forEach((step) => {
    const temp = next[step.r1][step.c1];
    next[step.r1][step.c1] = next[step.r2][step.c2];
    next[step.r2][step.c2] = temp;
  });
  return next;
}

function buildHorizontalFlipSteps(matrix) {
  const steps = [];
  const cols = matrix[0].length;
  for (let row = 0; row < matrix.length; row++) {
    for (let left = 0, right = cols - 1; left < right; left++, right--) {
      steps.push({
        row,
        leftCol: left,
        rightCol: right,
        r1: row,
        c1: left,
        r2: row,
        c2: right,
      });
    }
  }
  return steps;
}

function buildVerticalFlipSteps(matrix) {
  const steps = [];
  const cols = matrix[0].length;
  for (let top = 0, bottom = matrix.length - 1; top < bottom; top++, bottom--) {
    for (let col = 0; col < cols; col++) {
      steps.push({
        topRow: top,
        bottomRow: bottom,
        col,
        r1: top,
        c1: col,
        r2: bottom,
        c2: col,
      });
    }
  }
  return steps;
}

function buildRowSwapSteps(matrix, rowA, rowB) {
  const cols = matrix[0].length;
  return Array.from({ length: cols }, (_, col) => ({
    rowA,
    rowB,
    col,
    r1: rowA,
    c1: col,
    r2: rowB,
    c2: col,
  }));
}

function buildColSwapSteps(matrix, colA, colB) {
  return Array.from({ length: matrix.length }, (_, row) => ({
    colA,
    colB,
    row,
    r1: row,
    c1: colA,
    r2: row,
    c2: colB,
  }));
}

function buildWindowCases(matrix, sizes) {
  const rows = matrix.length;
  const cols = matrix[0].length;
  const cases = [];

  sizes.forEach((size) => {
    for (let row = 0; row <= rows - size; row++) {
      for (let col = 0; col <= cols - size; col++) {
        const entries = [];
        for (let x = row; x < row + size; x++) {
          for (let y = col; y < col + size; y++) {
            entries.push({
              row: x,
              col: y,
              value: matrix[x][y],
              label: `a[${x}][${y}]`,
            });
          }
        }
        cases.push({
          size,
          row,
          col,
          entries,
          localSum: sumNumberList(entries.map((entry) => entry.value)),
        });
      }
    }
  });

  return cases;
}

function buildCharWindowCases(matrix, size) {
  const rows = matrix.length;
  const cols = matrix[0].length;
  const cases = [];

  for (let top = 0; top <= rows - size; top++) {
    for (let left = 0; left <= cols - size; left++) {
      const entries = [];
      for (let row = top; row < top + size; row++) {
        for (let col = left; col < left + size; col++) {
          entries.push({
            row,
            col,
            value: matrix[row][col],
          });
        }
      }

      cases.push({
        top,
        left,
        bottom: top + size - 1,
        right: left + size - 1,
        entries,
      });
    }
  }

  return cases;
}

function buildWindowMatrixCells(matrix, currentWindow) {
  return flattenMatrixEntries(matrix).map((entry) => {
    if (!currentWindow) {
      return {
        label: entry.label,
        value: String(entry.value),
        state: "pending",
      };
    }

    const isInWindow = currentWindow.entries.some((item) => item.row === entry.row && item.col === entry.col);
    const isAnchor = currentWindow.row === entry.row && currentWindow.col === entry.col;
    return {
      label: entry.label,
      value: String(entry.value),
      state: isAnchor ? "window active" : isInWindow ? "window" : "pending",
    };
  });
}

function rowsToCharMatrix(rows) {
  return rows.map((row) => row.split(""));
}

function cellLabel(row, col) {
  return `a[${row}][${col}]`;
}

function cellMeta(index, cols) {
  const row = Math.floor(index / cols);
  const col = index % cols;
  return {
    index,
    row,
    col,
    label: `a[${row}][${col}]`,
  };
}
