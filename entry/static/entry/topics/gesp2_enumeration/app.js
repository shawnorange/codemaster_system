const pageState = {
  activeSectionId: null,
  activeDemoTarget: null,
  demoController: null,
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function loadSiteData() {
  const element = document.getElementById("gesp2-enumeration-site-data");
  if (!element) {
    return null;
  }

  try {
    return JSON.parse(element.textContent);
  } catch (error) {
    console.error("无法读取枚举法专题数据。", error);
    return null;
  }
}

function getOutlineLinks() {
  return [...document.querySelectorAll(".outline-anchor[data-target]")];
}

function findPreferredOutlineLink(targetId) {
  const links = getOutlineLinks();
  if (!links.length) {
    return null;
  }

  if (targetId === "demo-lab") {
    if (pageState.activeDemoTarget) {
      const demoLink = links.find(
        (link) =>
          link.dataset.target === "demo-lab" &&
          link.dataset.demoTarget === pageState.activeDemoTarget,
      );
      if (demoLink) {
        return demoLink;
      }
    }

    return links.find(
      (link) => link.dataset.target === "demo-lab" && !link.dataset.demoTarget,
    );
  }

  return links.find((link) => link.dataset.target === targetId) || null;
}

function applyActiveOutlineLink(targetId) {
  const links = getOutlineLinks();
  if (!links.length) {
    return;
  }

  links.forEach((link) => link.classList.remove("is-active"));
  const activeLink = findPreferredOutlineLink(targetId);
  if (activeLink) {
    activeLink.classList.add("is-active");
  }
}

function scrollToSection(sectionId, hash) {
  const target = document.getElementById(sectionId);
  if (!target) {
    return;
  }

  target.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });

  if (hash) {
    try {
      window.history.replaceState(null, "", hash);
    } catch (_error) {
      window.location.hash = hash;
    }
  }
}

function setupOutlineInteractions() {
  const links = getOutlineLinks();
  if (!links.length) {
    return;
  }

  links.forEach((link) => {
    link.addEventListener("click", (event) => {
      const sectionId = link.dataset.target;
      const href = link.getAttribute("href");

      if (!sectionId || !href || !href.startsWith("#")) {
        return;
      }

      event.preventDefault();

      if (link.dataset.demoTarget && pageState.demoController) {
        pageState.activeDemoTarget = link.dataset.demoTarget;
        pageState.demoController.activate(link.dataset.demoTarget);
      }

      pageState.activeSectionId = sectionId;
      applyActiveOutlineLink(sectionId);
      scrollToSection(sectionId, href);
    });
  });
}

function setupOutlineObserver() {
  const links = getOutlineLinks();
  const targetIds = [...new Set(links.map((link) => link.dataset.target).filter(Boolean))];
  const targets = targetIds
    .map((targetId) => document.getElementById(targetId))
    .filter(Boolean);

  if (!targets.length || typeof window.IntersectionObserver !== "function") {
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

      pageState.activeSectionId = visible.target.id;
      applyActiveOutlineLink(visible.target.id);
    },
    {
      rootMargin: "-18% 0px -60% 0px",
      threshold: [0.1, 0.24, 0.42, 0.6],
    },
  );

  targets.forEach((target) => observer.observe(target));
}

function renderChipRow(items) {
  if (!items?.length) {
    return "";
  }

  return items
    .map((item) => `<span class="enum-chip">${escapeHtml(item)}</span>`)
    .join("");
}

function renderTextParagraphs(lines) {
  if (!lines?.length) {
    return "";
  }

  return lines
    .map((line) => `<p class="question-card__prompt">${escapeHtml(line)}</p>`)
    .join("");
}

function renderQuestionOptions(options) {
  if (!options?.length) {
    return "";
  }

  return `
    <ol class="question-option-list" type="A">
      ${options
        .map((item) => `<li>${escapeHtml(String(item).replace(/^[A-D]\.\s*/, ""))}</li>`)
        .join("")}
    </ol>
  `;
}

function renderQuestionIo(question) {
  const items = [];
  if (question.input_format) {
    items.push(`
      <div class="question-io-card">
        <strong>输入格式</strong>
        <p>${escapeHtml(question.input_format)}</p>
      </div>
    `);
  }

  if (question.output_format) {
    items.push(`
      <div class="question-io-card">
        <strong>输出格式</strong>
        <p>${escapeHtml(question.output_format)}</p>
      </div>
    `);
  }

  if (question.sample_input) {
    items.push(`
      <div class="question-io-card">
        <strong>样例输入</strong>
        <pre><code>${escapeHtml(question.sample_input)}</code></pre>
      </div>
    `);
  }

  if (question.sample_output) {
    items.push(`
      <div class="question-io-card">
        <strong>样例输出</strong>
        <pre><code>${escapeHtml(question.sample_output)}</code></pre>
      </div>
    `);
  }

  if (!items.length) {
    return "";
  }

  return `<div class="question-io-grid">${items.join("")}</div>`;
}

function renderAnswerAnalysis(items) {
  if (!items?.length) {
    return "";
  }

  return items.map((item) => `<p>${escapeHtml(item)}</p>`).join("");
}

function renderQuestionCard(question) {
  if (!question) {
    return "";
  }

  return `
    <article class="question-card question-card--demo">
      <div class="question-card__meta">
        <span>${escapeHtml(question.source || "")}</span>
        <span>${escapeHtml(question.question_type || "")}</span>
        <span>${escapeHtml(question.question_no || "")}</span>
      </div>

      <div class="tag-row">
        <span class="tag">${escapeHtml(question.difficulty || "")}</span>
        <span class="tag tag--secondary">${escapeHtml(question.ability_point || "")}</span>
      </div>

      <h5>${escapeHtml(question.title || "")}</h5>

      <div class="question-statement">
        <strong>原题题面</strong>
        ${renderTextParagraphs(question.statement)}
        ${
          question.code
            ? `
              <div class="question-inline-code">
                <pre><code>${escapeHtml(question.code)}</code></pre>
              </div>
            `
            : ""
        }
        ${renderQuestionOptions(question.options)}
        ${renderQuestionIo(question)}
      </div>

      <dl class="question-notes">
        <div>
          <dt>分类理由</dt>
          <dd>${escapeHtml(question.classification_reason || "")}</dd>
        </div>
        <div>
          <dt>易错提醒</dt>
          <dd>${escapeHtml(question.pitfall || "")}</dd>
        </div>
        <div>
          <dt>推荐模板</dt>
          <dd>${escapeHtml(question.template_ref || "")}</dd>
        </div>
      </dl>

      <div class="question-answer-box">
        <button
          type="button"
          class="question-answer-toggle"
          data-answer-toggle
          aria-expanded="false"
        >
          显示答案
        </button>
        <div class="question-answer-panel" data-answer-panel hidden>
          <strong>参考答案</strong>
          ${
            question.answer_label
              ? `<p class="question-answer-label">${escapeHtml(question.answer_label)}</p>`
              : ""
          }
          ${question.answer ? `<p>${escapeHtml(question.answer)}</p>` : ""}
          ${renderAnswerAnalysis(question.answer_analysis)}
        </div>
      </div>
    </article>
  `;
}

function renderAnimationCards(cards) {
  return cards
    .map(
      (card) => `
        <article class="enum-animation-card">
          <span>${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
        </article>
      `,
    )
    .join("");
}

function renderDebugRows(rows) {
  return rows
    .map(
      (row) => `
        <div>
          <dt>${escapeHtml(row.label)}</dt>
          <dd>${escapeHtml(row.value)}</dd>
        </div>
      `,
    )
    .join("");
}

function renderPreviewRows(rows) {
  return rows
    .map((row) => `<div class="enum-preview-row">${escapeHtml(row)}</div>`)
    .join("");
}

function renderCodeLines(lines, activeLine) {
  return lines
    .map((line, index) => {
      const lineNumber = index + 1;
      const activeClass = lineNumber === Number(activeLine) ? " is-active" : "";
      return `
        <li class="enum-code-line${activeClass}">
          <span class="enum-code-line-number">${lineNumber}</span>
          <span class="enum-code-line-text">${escapeHtml(line)}</span>
        </li>
      `;
    })
    .join("");
}

function buildDemoMarkup(module, stepIndex) {
  const step = module.steps[stepIndex];
  const prevDisabled = stepIndex === 0 ? " disabled" : "";
  const nextDisabled = stepIndex === module.steps.length - 1 ? " disabled" : "";
  const relatedQuestions = module.related_question_cards || [];

  const stepButtons = module.steps
    .map((item, index) => {
      const activeClass = index === stepIndex ? " is-active" : "";
      return `
        <button
          type="button"
          class="enum-step-chip${activeClass}"
          data-step-index="${index}"
          aria-current="${index === stepIndex ? "step" : "false"}"
        >
          ${escapeHtml(item.label)}
        </button>
      `;
    })
    .join("");

  return `
    <section class="enum-player-shell">
      <div class="enum-player-head">
        <div class="enum-player-title">
          <p class="eyebrow">Demo Module</p>
          <h4>${escapeHtml(module.title)}</h4>
          <p>${escapeHtml(module.summary)}</p>
          <div class="enum-chip-row">${renderChipRow(module.tags)}</div>
        </div>

        <aside class="enum-player-summary">
          <strong>关联题目</strong>
          <div class="enum-chip-row">${renderChipRow(module.related_questions)}</div>
        </aside>
      </div>

      <div class="enum-player-controls">
        <div class="enum-step-row">${stepButtons}</div>
        <div class="enum-control-row">
          <button type="button" class="enum-control-button" data-demo-action="prev"${prevDisabled}>上一步</button>
          <button type="button" class="enum-control-button" data-demo-action="next"${nextDisabled}>下一步</button>
          <button type="button" class="enum-control-button" data-demo-action="reset">回到第 1 步</button>
        </div>
      </div>

      <div class="enum-player-layout">
        <article class="enum-player-panel enum-player-panel--animation">
          <div class="enum-panel-head">
            <strong>动画区</strong>
            <span>候选变化、命中状态和输出过程</span>
          </div>
          <div class="enum-animation-grid">${renderAnimationCards(step.animation_cards)}</div>
          <div class="enum-preview-panel">
            <strong>${escapeHtml(step.preview_title)}</strong>
            <div class="enum-preview-rows">${renderPreviewRows(step.preview_rows)}</div>
          </div>
        </article>

        <article class="enum-player-panel enum-player-panel--explain">
          <div class="enum-panel-copy">
            <span class="enum-step-badge">Step ${stepIndex + 1} / ${module.steps.length}</span>
            <h5>${escapeHtml(step.label)}</h5>
            <p>${escapeHtml(step.explanation)}</p>
          </div>
          <div class="enum-callout">
            <strong>教师讲解提示</strong>
            <p>${escapeHtml(step.teacher_prompt)}</p>
          </div>
        </article>

        <article class="enum-player-panel enum-player-panel--debug">
          <div class="enum-panel-head">
            <strong>Debug 区</strong>
            <span>当前变量和状态变化</span>
          </div>
          <dl class="enum-debug-list">${renderDebugRows(step.debug_rows)}</dl>
        </article>

        <article class="enum-player-panel enum-player-panel--code">
          <div class="enum-code-head">
            <strong>代码区</strong>
            <span>当前执行行已高亮</span>
          </div>
          <div class="enum-code-shell">
            <ol class="enum-code-lines">${renderCodeLines(module.code_lines, step.current_line)}</ol>
          </div>
        </article>
      </div>

      ${
        relatedQuestions.length
          ? `
            <section class="enum-related-section">
              <div class="enum-related-head">
                <strong>关联真题</strong>
                <span>本演示对应的完整题面与答案</span>
              </div>
              <div class="question-grid question-grid--demo">
                ${relatedQuestions.map((question) => renderQuestionCard(question)).join("")}
              </div>
            </section>
          `
          : ""
      }
    </section>
  `;
}

function setupDemoPlayer(siteData) {
  const tabs = [...document.querySelectorAll("[data-demo-tab]")];
  const stage = document.querySelector("[data-demo-stage]");
  const modules = siteData?.demo_modules || [];

  if (!tabs.length || !stage || !modules.length) {
    return;
  }

  const moduleMap = new Map(modules.map((module) => [module.id, module]));
  let activeModuleId = tabs.find((tab) => tab.classList.contains("is-active"))?.dataset.demoTab || modules[0].id;
  let activeStepIndex = 0;

  const syncTabs = () => {
    tabs.forEach((tab) => {
      const active = tab.dataset.demoTab === activeModuleId;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
  };

  const bindStageControls = () => {
    const module = moduleMap.get(activeModuleId);
    if (!module) {
      return;
    }

    stage.querySelectorAll("[data-step-index]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextIndex = Number(button.dataset.stepIndex);
        if (Number.isNaN(nextIndex)) {
          return;
        }
        activeStepIndex = nextIndex;
        render();
      });
    });

    stage.querySelectorAll("[data-demo-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.demoAction;
        if (action === "prev" && activeStepIndex > 0) {
          activeStepIndex -= 1;
        } else if (action === "next" && activeStepIndex < module.steps.length - 1) {
          activeStepIndex += 1;
        } else if (action === "reset") {
          activeStepIndex = 0;
        }
        render();
      });
    });
  };

  const render = () => {
    const module = moduleMap.get(activeModuleId);
    if (!module) {
      return;
    }

    if (activeStepIndex < 0 || activeStepIndex >= module.steps.length) {
      activeStepIndex = 0;
    }

    pageState.activeDemoTarget = module.id;
    syncTabs();
    stage.innerHTML = buildDemoMarkup(module, activeStepIndex);
    bindStageControls();

    if (pageState.activeSectionId === "demo-lab") {
      applyActiveOutlineLink("demo-lab");
    }
  };

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      activeModuleId = tab.dataset.demoTab;
      activeStepIndex = 0;
      render();
    });
  });

  pageState.demoController = {
    activate(moduleId) {
      if (!moduleMap.has(moduleId)) {
        return;
      }
      activeModuleId = moduleId;
      activeStepIndex = 0;
      render();
    },
  };

  render();
}

function setupStudentReview() {
  const shell = document.querySelector("[data-student-review-shell]");
  if (!shell) {
    return false;
  }

  const defaultKey = shell.dataset.defaultSection || "overview";
  const panels = [...shell.querySelectorAll("[data-review-panel]")];
  const panelMap = new Map(panels.map((panel) => [panel.dataset.reviewPanel, panel]));
  const navItems = [...shell.querySelectorAll(".review-nav-button[data-review-target]")];
  const select = shell.querySelector("[data-review-select]");
  const main = shell.querySelector("[data-review-main]");

  if (!panelMap.size) {
    return false;
  }

  const syncActiveControls = (activeKey) => {
    navItems.forEach((button) => {
      const isActive = button.dataset.reviewTarget === activeKey;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-current", isActive ? "page" : "false");
    });

    if (select) {
      select.value = activeKey;
    }
  };

  const updateHash = (activeKey) => {
    try {
      window.history.replaceState(null, "", `#${activeKey}`);
    } catch (_error) {
      window.location.hash = activeKey;
    }
  };

  const activatePanel = (activeKey, { updateLocation = true, scrollToTop = true } = {}) => {
    const resolvedKey = panelMap.has(activeKey) ? activeKey : defaultKey;

    panels.forEach((panel) => {
      const isActive = panel.dataset.reviewPanel === resolvedKey;
      panel.hidden = !isActive;
      panel.classList.toggle("is-active", isActive);
    });

    syncActiveControls(resolvedKey);

    if (updateLocation) {
      updateHash(resolvedKey);
    }

    if (scrollToTop) {
      (main || shell).scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  };

  shell.addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-target]");
    if (!button || !shell.contains(button)) {
      return;
    }

    const targetKey = button.dataset.reviewTarget;
    if (!targetKey) {
      return;
    }

    activatePanel(targetKey);
  });

  if (select) {
    select.addEventListener("change", () => {
      activatePanel(select.value);
    });
  }

  const initialHash = window.location.hash.replace("#", "");
  activatePanel(initialHash || defaultKey, {
    updateLocation: Boolean(initialHash),
    scrollToTop: false,
  });

  return true;
}

function setupAnswerToggles() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-answer-toggle]");
    if (!button) {
      return;
    }

    const wrapper = button.closest(".question-answer-box");
    const panel = wrapper?.querySelector("[data-answer-panel]");
    if (!panel) {
      return;
    }

    if (!panel.hasAttribute("hidden")) {
      return;
    }

    panel.removeAttribute("hidden");
    button.setAttribute("aria-expanded", "true");
    button.textContent = "答案已显示";
    button.disabled = true;
    button.classList.add("is-locked");
  });
}

function init() {
  const isStudentReview = setupStudentReview();
  if (!isStudentReview) {
    const siteData = loadSiteData();
    setupOutlineInteractions();
    setupOutlineObserver();
    setupDemoPlayer(siteData);

    const initialHash = window.location.hash.replace("#", "");
    if (initialHash) {
      pageState.activeSectionId = initialHash;
      applyActiveOutlineLink(initialHash);
    } else {
      applyActiveOutlineLink("topic-overview");
    }
  }
  setupAnswerToggles();
}

init();
