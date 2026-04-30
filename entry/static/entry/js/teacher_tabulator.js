(function () {
    function readJsonScript(id) {
        var element = document.getElementById(id);
        if (!element) {
            return [];
        }
        try {
            return JSON.parse(element.textContent);
        } catch (error) {
            console.error("Failed to parse table data:", id, error);
            return [];
        }
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function debounce(fn, delay) {
        var timer = null;
        return function () {
            var args = arguments;
            clearTimeout(timer);
            timer = setTimeout(function () {
                fn.apply(null, args);
            }, delay);
        };
    }

    function buildGridStateKey(scope) {
        return "codemaster:grid-state:" + scope + ":" + window.location.pathname;
    }

    function loadGridState(scope) {
        try {
            var raw = window.sessionStorage.getItem(buildGridStateKey(scope));
            return raw ? JSON.parse(raw) : {};
        } catch (error) {
            return {};
        }
    }

    function saveGridState(scope, state) {
        try {
            window.sessionStorage.setItem(buildGridStateKey(scope), JSON.stringify(state));
        } catch (error) {
            console.warn("Failed to persist grid state", scope, error);
        }
    }

    function getCurrentPageSize(table, container) {
        try {
            if (typeof table.getPageSize === "function") {
                return Number(table.getPageSize()) || 10;
            }
        } catch (error) {
            // ignore and fall back to DOM
        }
        var select = container ? container.querySelector(".tabulator-page-size") : null;
        return select ? Number(select.value) || 10 : 10;
    }

    function getCurrentPage(table) {
        try {
            if (typeof table.getPage === "function") {
                return Number(table.getPage()) || 1;
            }
        } catch (error) {
            return 1;
        }
        return 1;
    }

    function actionButton(label, href, variant, title) {
        var classes = ["cm-tabulator-btn"];
        if (variant) {
            classes.push("cm-tabulator-btn--" + variant);
        }
        var titleAttr = title ? ' title="' + escapeHtml(title) + '"' : "";
        return '<a class="' + classes.join(" ") + '" href="' + escapeHtml(href || "#") + '"' + titleAttr + ">" + escapeHtml(label) + "</a>";
    }

    function statusPill(label, variant) {
        return '<span class="cm-tabulator-pill cm-tabulator-pill--' + escapeHtml(variant) + '">' + escapeHtml(label) + "</span>";
    }

    function defaultOptions(data, columns, extraOptions) {
        return Object.assign(
            {
                data: data,
                layout: "fitColumns",
                responsiveLayout: false,
                pagination: true,
                paginationMode: "local",
                paginationSize: 10,
                paginationSizeSelector: [10, 15, 50, 100],
                paginationCounter: "rows",
                placeholder: "暂无数据",
                columnDefaults: {
                    headerHozAlign: "left",
                    vertAlign: "middle",
                    resizable: true,
                    minWidth: 90,
                    tooltip: function (e, cell) {
                        var value = cell.getValue();
                        return typeof value === "string" && value ? value : false;
                    },
                },
                columns: columns,
            },
            extraOptions || {}
        );
    }

    function buildSingleSelectTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var hiddenInput = document.getElementById(config.hiddenInputId);
        var locked = !!config.locked;

        function selectedValue() {
            return hiddenInput ? String(hiddenInput.value || "") : "";
        }

        function syncSelection(rowData) {
            if (hiddenInput && rowData) {
                hiddenInput.value = String(rowData[config.valueField] || "");
            }
        }

        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                config.columns,
                {
                    index: config.valueField,
                    selectableRows: 1,
                    rowClick: function (e, row) {
                        if (locked) {
                            return;
                        }
                        row.select();
                    },
                }
            )
        );

        table.on("tableBuilt", function () {
            var presetValue = selectedValue();
            var selectedRow = data.find(function (row) {
                return String(row[config.valueField] || "") === presetValue;
            }) || data.find(function (row) {
                return !!row.selected;
            });
            if (selectedRow) {
                table.selectRow([selectedRow[config.valueField]]);
                syncSelection(selectedRow);
            }
        });

        table.on("rowSelectionChanged", function (selectedData) {
            if (selectedData.length) {
                syncSelection(selectedData[0]);
            }
        });

        var searchFilter = attachSearch(table, config.searchInputId, config.searchFields || []);
        if (config.persistScope) {
            wirePersistentState(config.persistScope, table, document.getElementById(config.tableId), config.searchInputId, searchFilter);
        }
        return table;
    }

    function attachSearch(table, inputId, fields) {
        var input = document.getElementById(inputId);
        if (!input) {
            return function () {};
        }

        var applyFilter = debounce(function () {
            var keyword = (input.value || "").trim().toLowerCase();
            if (!keyword) {
                table.clearFilter(true);
                return;
            }

            table.setFilter(function (rowData) {
                return fields.some(function (field) {
                    return String(rowData[field] || "").toLowerCase().indexOf(keyword) > -1;
                });
            });
        }, 120);

        input.addEventListener("input", applyFilter);

        if ((input.value || "").trim()) {
            table.on("tableBuilt", function () {
                applyFilter();
            });
        }

        return applyFilter;
    }

    function wirePersistentState(scope, table, container, inputId, applySearchFilter) {
        var input = document.getElementById(inputId);
        var state = loadGridState(scope);

        function persist() {
            saveGridState(scope, {
                search: input ? input.value || "" : "",
                pageSize: getCurrentPageSize(table, container),
                page: getCurrentPage(table),
            });
        }

        table.on("tableBuilt", function () {
            var restored = loadGridState(scope);
            if (input && restored.search) {
                input.value = restored.search;
            }
            if (restored.pageSize && typeof table.setPageSize === "function") {
                table.setPageSize(restored.pageSize);
            }
            if (applySearchFilter) {
                applySearchFilter();
            }
            if (restored.page && restored.page > 1 && typeof table.setPage === "function") {
                setTimeout(function () {
                    table.setPage(restored.page);
                }, 60);
            }
            persist();
        });

        if (input) {
            input.addEventListener("input", function () {
                setTimeout(persist, 160);
            });
        }

        if (container) {
            container.addEventListener("click", function (event) {
                if (event.target.closest(".tabulator-page")) {
                    setTimeout(persist, 80);
                }
            });
            container.addEventListener("change", function (event) {
                if (event.target.closest(".tabulator-page-size")) {
                    setTimeout(persist, 80);
                }
            });
        }

        return {
            persist: persist,
            initialState: state,
        };
    }

    function buildCourseStudentsTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(data, [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Student",
                    columns: [
                        { title: "姓名", field: "name", minWidth: 180 },
                        { title: "年级", field: "grade", hozAlign: "center", width: 120 },
                    ],
                },
                { title: "Current Scope", field: "program", minWidth: 300 },
                {
                    title: "Action",
                    columns: [
                        {
                            title: "详情",
                            field: "action_href",
                            hozAlign: "center",
                            width: 120,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton(row.action_label || "详情", row.action_href, "primary");
                            },
                        },
                        {
                            title: "关系",
                            field: "relation_href",
                            hozAlign: "center",
                            width: 120,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton(row.relation_label || "关系", row.relation_href);
                            },
                        },
                    ],
                },
            ])
        );

        attachSearch(table, config.searchInputId, ["name"]);
        return table;
    }

    function buildTeacherWorkbenchStudentsTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(data, [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Student",
                    columns: [
                        { title: "姓名", field: "name", minWidth: 160 },
                        { title: "年级", field: "grade", hozAlign: "center", width: 110 },
                        { title: "家长手机", field: "parent_phone", hozAlign: "center", minWidth: 150 },
                    ],
                },
                { title: "当前方向 / 当前课程", field: "program", minWidth: 280 },
                {
                    title: "Teaching",
                    columns: [
                        {
                            title: "已开放专题",
                            field: "open_topics",
                            hozAlign: "center",
                            width: 120,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return statusPill(row.open_topics || "0/0", row.state === "open" ? "open" : "locked");
                            },
                        },
                        { title: "所剩课时", field: "remaining_hours", hozAlign: "center", width: 120 },
                    ],
                },
                {
                    title: "Action",
                    columns: [
                        {
                            title: "详情",
                            field: "action_href",
                            hozAlign: "center",
                            width: 110,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton(row.action_label || "详情", row.action_href, "primary");
                            },
                        },
                        {
                            title: "关系",
                            field: "relation_href",
                            hozAlign: "center",
                            width: 110,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton(row.relation_label || "关系", row.relation_href);
                            },
                        },
                    ],
                },
            ])
        );

        var searchFilter = attachSearch(table, config.searchInputId, ["name", "grade", "parent_phone", "program"]);
        wirePersistentState("teacher-workbench-students", table, document.getElementById(config.tableId), config.searchInputId, searchFilter);
        return table;
    }

    function attachTeacherHomeworkStatsFilters(table, config) {
        var searchInput = document.getElementById(config.searchInputId);
        var levelInput = document.getElementById(config.levelFilterId);
        var searchFields = [
            "student_name",
            "level_code_display",
            "knowledge_point",
            "completion_rate_text",
            "overall_correct_rate_text",
            "answer_rate_search_text",
        ];

        function matchesSearch(rowData, keyword) {
            if (!keyword) {
                return true;
            }
            return searchFields.some(function (field) {
                return String(rowData[field] || "").toLowerCase().indexOf(keyword) > -1;
            });
        }

        function applyFilters(resetPage) {
            var keyword = searchInput ? String(searchInput.value || "").trim().toLowerCase() : "";
            var selectedLevel = levelInput ? String(levelInput.value || "all") : "all";
            if (!keyword && (!selectedLevel || selectedLevel === "all")) {
                table.clearFilter(true);
            } else {
                table.setFilter(function (rowData) {
                    var matchesLevel =
                        !selectedLevel || selectedLevel === "all"
                            ? true
                            : String(rowData.level_code_filter_value || "__ungrouped__") === selectedLevel;
                    return matchesLevel && matchesSearch(rowData, keyword);
                });
            }

            if (resetPage && typeof table.setPage === "function") {
                try {
                    table.setPage(1);
                } catch (error) {
                    // Ignore pagination reset issues when the table is rebuilding.
                }
            }
        }

        var applySearchFilter = debounce(function () {
            applyFilters(true);
        }, 120);

        if (searchInput) {
            searchInput.addEventListener("input", applySearchFilter);
        }
        if (levelInput) {
            levelInput.addEventListener("change", function () {
                applyFilters(true);
            });
        }

        table.on("tableBuilt", function () {
            applyFilters(false);
        });

        return function () {
            applyFilters(false);
        };
    }

    function renderTeacherHomeworkStatsRateCell(rowData) {
        var details = Array.isArray(rowData.answer_rate_details) ? rowData.answer_rate_details : [];
        if (!details.length) {
            return '<div class="teacher-homework-stats-datagrid__empty">暂无答题统计</div>';
        }

        return (
            '<div class="teacher-homework-stats-datagrid__rates">' +
            details
                .map(function (detail) {
                    var knowledgePoint =
                        detail.knowledge_point || detail.knowledge_point_name || "";
                    var knowledgePointPrefix =
                        detail.show_knowledge_point_name && knowledgePoint
                            ? escapeHtml(knowledgePoint) + "："
                            : "";
                    return (
                        '<div class="teacher-homework-stats-datagrid__rate-item">' +
                        knowledgePointPrefix +
                        "正确 " +
                        escapeHtml(detail.correct_count || 0) +
                        "，错误 " +
                        escapeHtml(detail.wrong_count || 0) +
                        "，正确率 " +
                        escapeHtml(detail.correct_rate_text || "0%") +
                        "，错误率 " +
                        escapeHtml(detail.wrong_rate_text || "0%") +
                        "</div>"
                    );
                })
                .join("") +
            "</div>"
        );
    }

    function renderMultilineText(value) {
        return escapeHtml(value || "").replace(/\n/g, "<br>");
    }

    function buildTeacherHomeworkStatsStudentsTable(config) {
        var container = document.getElementById(config.tableId);
        if (!container || typeof Tabulator !== "function") {
            return null;
        }

        var rawData = readJsonScript(config.dataScriptId);
        var data = Array.isArray(rawData) ? rawData : [];
        var period = String(config.period || "week").toLowerCase();
        var isWeek = period === "week";
        var hasSubmissionDetail = isWeek || period === "month";
        var columns = [
            { title: "学生姓名", field: "student_name", minWidth: 140 },
            { title: "级别", field: "level_code_display", hozAlign: "center", width: 108 },
        ];
        if (isWeek) {
            columns.push({ title: "知识点", field: "knowledge_point", minWidth: 148 });
        }
        columns = columns.concat([
            { title: "应交作业数", field: "assigned_count", sorter: "number", hozAlign: "center", width: 112 },
            { title: "已完成作业数", field: "submitted_count", sorter: "number", hozAlign: "center", width: 112 },
            { title: "待完成作业数", field: "pending_count", sorter: "number", hozAlign: "center", width: 118 },
            { title: "超期未完成作业数", field: "overdue_missing_count", sorter: "number", hozAlign: "center", width: 132 },
            {
                title: "完成率",
                field: "completion_rate",
                sorter: "number",
                hozAlign: "center",
                width: 112,
                formatter: function (cell) {
                    return escapeHtml(cell.getRow().getData().completion_rate_text || "0%");
                },
            },
            {
                title: "正确率 / 错误率",
                field: "answer_rate_search_text",
                minWidth: 280,
                widthGrow: 2.4,
                headerSort: false,
                cssClass: "teacher-homework-stats-datagrid__cell--rates",
                formatter: function (cell) {
                    return renderTeacherHomeworkStatsRateCell(cell.getRow().getData());
                },
            },
        ]);
        if (hasSubmissionDetail) {
            columns.push(
                {
                    title: "Homework Submission Detail",
                    field: "submission_record_count_text",
                    minWidth: 188,
                    headerSort: false,
                    cssClass: "teacher-homework-stats-datagrid__cell--submission-detail",
                    formatter: function (cell) {
                        var row = cell.getRow().getData();
                        var parts = [];
                        if (row.submission_record_count_text) {
                            parts.push('<div class="teacher-homework-stats-datagrid__submission-count">' + escapeHtml(row.submission_record_count_text) + "</div>");
                        }
                        if (row.detail_href) {
                            parts.push(actionButton(row.detail_label || "查看详情", row.detail_href, "primary"));
                        }
                        return parts.join("");
                    },
                }
            );
        }
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                columns,
                {
                    paginationSize: 15,
                }
            )
        );

        var searchFilter = attachTeacherHomeworkStatsFilters(table, config);
        wirePersistentState(
            "teacher-homework-stats-students",
            table,
            container,
            config.searchInputId,
            searchFilter
        );
        return table;
    }

    function buildTeacherHomeworkSubmissionDetailTable(config) {
        var container = document.getElementById(config.tableId);
        if (!container || typeof Tabulator !== "function") {
            return null;
        }

        var rawData = readJsonScript(config.dataScriptId);
        var data = Array.isArray(rawData) ? rawData : [];
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    { title: "提交时间", field: "submitted_at_text", minWidth: 172 },
                    { title: "知识点", field: "knowledge_point", minWidth: 148 },
                    { title: "correct_count", field: "correct_count", sorter: "number", hozAlign: "center", width: 96 },
                    { title: "wrong_count", field: "wrong_count", sorter: "number", hozAlign: "center", width: 96 },
                    {
                        title: "正确率",
                        field: "correct_rate",
                        sorter: "number",
                        hozAlign: "center",
                        width: 112,
                        formatter: function (cell) {
                            return escapeHtml(cell.getRow().getData().correct_rate_text || "暂无统计");
                        },
                    },
                    {
                        title: "查看做题详情",
                        field: "detail_answer_href",
                        hozAlign: "center",
                        width: 132,
                        headerSort: false,
                        formatter: function (cell) {
                            var row = cell.getRow().getData();
                            return row.detail_answer_href ? actionButton("查看做题详情", row.detail_answer_href, "primary") : "";
                        },
                    },
                ],
                {
                    index: "row_key",
                    paginationSize: 15,
                }
            )
        );

        var searchFilter = attachSearch(
            table,
            config.searchInputId,
            [
                "submitted_at_text",
                "knowledge_point",
                "correct_count",
                "wrong_count",
                "correct_rate_text",
                "submission_rate_search_text",
            ]
        );
        wirePersistentState(
            "teacher-homework-submission-detail",
            table,
            container,
            config.searchInputId,
            searchFilter
        );
        return table;
    }

    function buildTeacherHomeworkAssignmentSubmissionDetailTable(config) {
        var container = document.getElementById(config.tableId);
        if (!container || typeof Tabulator !== "function") {
            return null;
        }

        var rawData = readJsonScript(config.dataScriptId);
        var data = Array.isArray(rawData) ? rawData : [];
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    { title: "提交记录 ID", field: "submission_id", sorter: "number", hozAlign: "center", width: 108 },
                    { title: "assignment_id", field: "assignment_id", sorter: "number", hozAlign: "center", width: 112 },
                    { title: "作业 / 知识点名称", field: "knowledge_point", minWidth: 160, widthGrow: 1.4 },
                    { title: "status", field: "status_text", minWidth: 112, hozAlign: "center" },
                    { title: "total_count", field: "total_count", sorter: "number", hozAlign: "center", width: 98 },
                    { title: "correct_count", field: "correct_count", sorter: "number", hozAlign: "center", width: 102 },
                    { title: "wrong_count", field: "wrong_count", sorter: "number", hozAlign: "center", width: 102 },
                    { title: "score", field: "score_text", hozAlign: "center", width: 96 },
                    { title: "started_at", field: "started_at_text", minWidth: 152 },
                    { title: "submitted_at", field: "submitted_at_text", minWidth: 152 },
                    { title: "checked_at", field: "checked_at_text", minWidth: 152 },
                    { title: "created_at", field: "created_at_text", minWidth: 152 },
                    {
                        title: "操作",
                        field: "detail_answer_href",
                        hozAlign: "center",
                        width: 138,
                        headerSort: false,
                        formatter: function (cell) {
                            var row = cell.getRow().getData();
                            return row.detail_answer_href ? actionButton("查看逐题详情", row.detail_answer_href, "primary") : "";
                        },
                    },
                ],
                {
                    index: "row_key",
                    paginationSize: 15,
                }
            )
        );

        var searchFilter = attachSearch(
            table,
            config.searchInputId,
            [
                "submission_id",
                "assignment_id",
                "knowledge_point",
                "status_text",
                "total_count",
                "correct_count",
                "wrong_count",
                "score_text",
                "started_at_text",
                "submitted_at_text",
                "checked_at_text",
                "created_at_text",
                "submission_search_text",
            ]
        );
        wirePersistentState(
            "teacher-homework-assignment-submission-detail",
            table,
            container,
            config.searchInputId,
            searchFilter
        );
        return table;
    }

    function buildTeacherHomeworkSubmissionAnswerDetailTable(config) {
        var container = document.getElementById(config.tableId);
        if (!container || typeof Tabulator !== "function") {
            return null;
        }

        var rawData = readJsonScript(config.dataScriptId);
        var data = Array.isArray(rawData) ? rawData : [];
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    { title: "提交时间", field: "submitted_at_text", minWidth: 172 },
                    { title: "知识点", field: "knowledge_point", minWidth: 148 },
                    {
                        title: "题目详情",
                        field: "question_detail",
                        minWidth: 420,
                        widthGrow: 2.6,
                        headerSort: false,
                        cssClass: "teacher-homework-stats-datagrid__cell--multiline",
                        formatter: function (cell) {
                            return '<div class="teacher-homework-stats-datagrid__multiline">' + renderMultilineText(cell.getValue()) + "</div>";
                        },
                    },
                ],
                {
                    index: "row_key",
                    paginationSize: 15,
                }
            )
        );

        var searchFilter = attachSearch(
            table,
            config.searchInputId,
            [
                "submitted_at_text",
                "knowledge_point",
                "question_detail_search_text",
            ]
        );
        wirePersistentState(
            "teacher-homework-submission-answer-detail",
            table,
            container,
            config.searchInputId,
            searchFilter
        );
        return table;
    }

    function buildTeacherWorkbenchCoursesTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(data, [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Course",
                    columns: [
                        { title: "级别", field: "level", hozAlign: "center", width: 120 },
                        { title: "课程名称", field: "title", minWidth: 220 },
                    ],
                },
                {
                    title: "Stats",
                    columns: [
                        { title: "负责学生", field: "student_count", sorter: "number", hozAlign: "center", width: 120 },
                        {
                            title: "已开放内容",
                            field: "open_content_count",
                            sorter: "number",
                            hozAlign: "center",
                            width: 120,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return statusPill(String(row.open_content_count || 0), row.open_content_count ? "open" : "locked");
                            },
                        },
                    ],
                },
                {
                    title: "Action",
                    columns: [
                        {
                            title: "查看",
                            field: "action_href",
                            hozAlign: "center",
                            width: 120,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton(row.action_label || "查看", row.action_href, "primary", row.note);
                            },
                        },
                    ],
                },
            ])
        );

        var searchFilter = attachSearch(table, config.searchInputId, ["title", "level", "note"]);
        wirePersistentState("teacher-workbench-courses", table, document.getElementById(config.tableId), config.searchInputId, searchFilter);
        return table;
    }

    function buildAssignmentStudentSelector(config) {
        return buildSingleSelectTable({
            tableId: config.tableId,
            dataScriptId: config.dataScriptId,
            searchInputId: config.searchInputId,
            hiddenInputId: config.hiddenInputId,
            valueField: config.valueField,
            locked: config.locked,
            searchFields: ["name", "grade", "parent_phone", "current_scope_text"],
            columns: [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Student",
                    columns: [
                        { title: "姓名", field: "name", minWidth: 180 },
                        { title: "年级", field: "grade", hozAlign: "center", width: 120 },
                        { title: "家长手机", field: "parent_phone", hozAlign: "center", minWidth: 150 },
                    ],
                },
                { title: "当前范围", field: "current_scope_text", minWidth: 320 },
            ],
        });
    }

    function buildAssignmentScopeSelector(config) {
        return buildSingleSelectTable({
            tableId: config.tableId,
            dataScriptId: config.dataScriptId,
            searchInputId: config.searchInputId,
            hiddenInputId: config.hiddenInputId,
            valueField: config.valueField,
            searchFields: ["course_title", "course_slug", "level_code", "label"],
            columns: [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Course Scope",
                    columns: [
                        { title: "课程方向", field: "course_title", minWidth: 180 },
                        { title: "级别", field: "level_code", hozAlign: "center", width: 120 },
                    ],
                },
                { title: "范围说明", field: "label", minWidth: 220 },
            ],
        });
    }

    function buildKnowledgeLevelSelector(config) {
        return buildSingleSelectTable({
            tableId: config.tableId,
            dataScriptId: config.dataScriptId,
            searchInputId: config.searchInputId,
            hiddenInputId: config.hiddenInputId,
            valueField: config.valueField,
            searchFields: ["code", "title", "summary"],
            columns: [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Level",
                    columns: [
                        { title: "编码", field: "code", hozAlign: "center", width: 120 },
                        { title: "名称", field: "title", minWidth: 160 },
                    ],
                },
                { title: "说明", field: "summary", minWidth: 320 },
            ],
        });
    }

    function buildStudentAssignmentsTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(data, [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Assignment",
                    columns: [
                        { title: "课程方向", field: "course_title", minWidth: 180 },
                        { title: "级别", field: "level_code", hozAlign: "center", width: 120 },
                    ],
                },
                {
                    title: "Status",
                    columns: [
                        {
                            title: "状态",
                            field: "status_text",
                            hozAlign: "center",
                            width: 120,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return statusPill(row.status_text, row.can_edit ? "success" : "muted");
                            },
                        },
                        { title: "更新时间", field: "updated_at_text", minWidth: 170 },
                    ],
                },
                {
                    title: "Action",
                    columns: [
                        {
                            title: "修改",
                            field: "edit_href",
                            hozAlign: "center",
                            width: 96,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return row.can_edit ? actionButton("修改", row.edit_href) : statusPill("历史记录", "muted");
                            },
                        },
                        {
                            title: "移除",
                            field: "remove_href",
                            hozAlign: "center",
                            width: 96,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return row.can_edit ? actionButton("移除", row.remove_href) : "";
                            },
                        },
                    ],
                },
            ])
        );

        var searchFilter = attachSearch(table, config.searchInputId, ["course_title", "level_code", "status_text"]);
        wirePersistentState("student-assignment-grid", table, document.getElementById(config.tableId), config.searchInputId, searchFilter);
        return table;
    }

    function buildLevelTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                    {
                        title: "Level Info",
                        columns: [
                            { title: "Level", field: "title", minWidth: 140 },
                            { title: "说明", field: "summary", minWidth: 320 },
                        ],
                    },
                    {
                        title: "Stats",
                        columns: [
                            { title: "知识点", field: "knowledge_point_count", sorter: "number", hozAlign: "center", width: 120 },
                            { title: "真实内容", field: "real_content_count", sorter: "number", hozAlign: "center", width: 120 },
                        ],
                    },
                    {
                        title: "Action",
                        columns: [
                            {
                                title: "查看",
                                field: "action_href",
                                hozAlign: "center",
                                width: 120,
                                headerSort: false,
                                formatter: function (cell) {
                                    var row = cell.getRow().getData();
                                    return actionButton("查看", row.action_href, "primary");
                                },
                            },
                        ],
                    },
                ],
                {
                    initialSort: [{ column: "title", dir: "asc" }],
                }
            )
        );

        attachSearch(table, config.searchInputId, ["title", "summary"]);
        return table;
    }

    function buildKnowledgePointTable(config) {
        var data = readJsonScript(config.dataScriptId);
        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(data, [
                { title: "#", formatter: "rownum", hozAlign: "center", width: 72, headerSort: false },
                {
                    title: "Basic",
                    columns: [
                        { title: "Title", field: "title", minWidth: 220 },
                        { title: "Slug", field: "slug", minWidth: 180 },
                        {
                            title: "真实内容",
                            field: "has_real_content_text",
                            width: 120,
                            hozAlign: "center",
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return statusPill(row.has_real_content_text, row.has_real_content ? "success" : "muted");
                            },
                        },
                    ],
                },
                {
                    title: "Actions",
                    columns: [
                        {
                            title: "学生权限",
                            field: "permission_href",
                            hozAlign: "center",
                            width: 120,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton(row.permission_label || "管理", row.permission_href, "", row.permission_text);
                            },
                        },
                        {
                            title: "Teaching Page",
                            field: "teaching_page_href",
                            hozAlign: "center",
                            width: 132,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                var variant = row.teaching_page_label === "进入" ? "primary" : "";
                                return actionButton(row.teaching_page_label, row.teaching_page_href, variant, row.teaching_page_status_text);
                            },
                        },
                        {
                            title: "修改",
                            field: "edit_href",
                            hozAlign: "center",
                            width: 96,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton("修改", row.edit_href);
                            },
                        },
                        {
                            title: "删除",
                            field: "delete_href",
                            hozAlign: "center",
                            width: 96,
                            headerSort: false,
                            formatter: function (cell) {
                                var row = cell.getRow().getData();
                                return actionButton("删除", row.delete_href);
                            },
                        },
                    ],
                },
            ])
        );

        var searchFilter = attachSearch(table, config.searchInputId, ["title"]);
        wirePersistentState("knowledge-grid", table, document.getElementById(config.tableId), config.searchInputId, searchFilter);
        return table;
    }

    function buildPermissionsTable(config) {
        var data = readJsonScript(config.dataScriptId).slice().sort(function (left, right) {
            if (left.is_open === right.is_open) {
                return String(left.name || "").localeCompare(String(right.name || ""), "zh-Hans-CN");
            }
            return left.is_open ? -1 : 1;
        });
        var hiddenContainer = document.getElementById(config.hiddenContainerId);
        var form = document.getElementById(config.formId);
        var selectedCounter = document.getElementById(config.selectedCountId);
        var totalCounter = document.getElementById(config.totalCountId);
        var openCounter = document.getElementById(config.openCountId);
        var lockedCounter = document.getElementById(config.lockedCountId);
        var filterButtons = {
            all: document.getElementById(config.filterAllButtonId),
            open: document.getElementById(config.filterOpenButtonId),
            locked: document.getElementById(config.filterLockedButtonId),
        };
        var selectAllButton = document.getElementById(config.selectAllButtonId);
        var clearSelectionButton = document.getElementById(config.clearSelectionButtonId);
        var filterState = {
            search: (document.getElementById(config.searchInputId)?.value || "").trim().toLowerCase(),
            status: "all",
        };

        function toggleRowSelection(row) {
            if (row.isSelected()) {
                row.deselect();
            } else {
                row.select();
            }
        }

        function applyFilters(table) {
            if (!filterState.search && filterState.status === "all") {
                table.clearFilter(true);
                return;
            }
            table.setFilter(function (rowData) {
                var matchesSearch = !filterState.search || ["name", "grade", "scope_text"].some(function (field) {
                    return String(rowData[field] || "").toLowerCase().indexOf(filterState.search) > -1;
                });
                var matchesStatus =
                    filterState.status === "all" ||
                    (filterState.status === "open" && rowData.is_open) ||
                    (filterState.status === "locked" && !rowData.is_open);
                return matchesSearch && matchesStatus;
            });
        }

        function updateFilterButtons() {
            Object.keys(filterButtons).forEach(function (key) {
                var button = filterButtons[key];
                if (!button) {
                    return;
                }
                button.classList.toggle("cm-tabulator-btn--primary", filterState.status === key);
            });
        }

        function updateCounters(table) {
            if (selectedCounter) {
                selectedCounter.textContent = String(table.getSelectedData().length);
            }
            if (totalCounter) {
                totalCounter.textContent = String(data.length);
            }
            if (openCounter) {
                openCounter.textContent = String(data.filter(function (row) { return row.is_open; }).length);
            }
            if (lockedCounter) {
                lockedCounter.textContent = String(data.filter(function (row) { return !row.is_open; }).length);
            }
        }

        function syncHiddenInputs(table) {
            if (!hiddenContainer) {
                return;
            }
            hiddenContainer.innerHTML = "";
            table.getSelectedData().forEach(function (row) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "student_ids";
                input.value = row.student_id;
                hiddenContainer.appendChild(input);
            });
        }

        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    {
                        title: "Student",
                        columns: [
                            { title: "姓名", field: "name", minWidth: 180 },
                            { title: "年级", field: "grade", hozAlign: "center", width: 120 },
                        ],
                    },
                    { title: "负责范围", field: "scope_text", minWidth: 300 },
                    {
                        title: "Status",
                        columns: [
                            {
                                title: "当前状态",
                                field: "status_text",
                                hozAlign: "center",
                                width: 120,
                                formatter: function (cell) {
                                    var row = cell.getRow().getData();
                                    return statusPill(row.status_text, row.is_open ? "open" : "locked");
                                },
                            },
                            { title: "最近记录", field: "hint", minWidth: 220 },
                        ],
                    },
                ],
                {
                    index: "student_id",
                    selectableRows: true,
                    selectableRowsPersistence: true,
                    rowHeader: {
                        formatter: "rowSelection",
                        titleFormatter: "rowSelection",
                        width: 56,
                        hozAlign: "center",
                        headerHozAlign: "center",
                        resizable: false,
                        frozen: true,
                        headerSort: false,
                        cellClick: function (e, cell) {
                            toggleRowSelection(cell.getRow());
                        },
                    },
                    rowClick: function (e, row) {
                        toggleRowSelection(row);
                    },
                }
            )
        );

        table.on("tableBuilt", function () {
            var selectedIds = data.filter(function (row) { return row.selected; }).map(function (row) { return row.student_id; });
            if (selectedIds.length) {
                table.selectRow(selectedIds);
            }
            updateCounters(table);
            syncHiddenInputs(table);
        });

        table.on("rowSelectionChanged", function () {
            updateCounters(table);
            syncHiddenInputs(table);
        });

        var searchInput = document.getElementById(config.searchInputId);
        if (searchInput) {
            searchInput.addEventListener("input", debounce(function () {
                filterState.search = (searchInput.value || "").trim().toLowerCase();
                applyFilters(table);
            }, 120));
        }

        Object.keys(filterButtons).forEach(function (key) {
            var button = filterButtons[key];
            if (!button) {
                return;
            }
            button.addEventListener("click", function () {
                filterState.status = key;
                updateFilterButtons();
                applyFilters(table);
            });
        });

        if (selectAllButton) {
            selectAllButton.addEventListener("click", function () {
                table.getRows("active").forEach(function (row) {
                    row.select();
                });
            });
        }

        if (clearSelectionButton) {
            clearSelectionButton.addEventListener("click", function () {
                table.deselectRow();
            });
        }

        table.on("tableBuilt", function () {
            updateFilterButtons();
            applyFilters(table);
        });

        if (form) {
            form.addEventListener("submit", function () {
                syncHiddenInputs(table);
            });
        }

        return table;
    }

    function buildCourseStudentPoolTable(config) {
        var data = readJsonScript(config.dataScriptId).slice().sort(function (left, right) {
            return String(left.name || "").localeCompare(String(right.name || ""), "zh-Hans-CN");
        });
        var hiddenContainer = document.getElementById(config.hiddenContainerId);
        var form = document.getElementById(config.formId);
        var selectedCounter = document.getElementById(config.selectedCountId);
        var totalCounter = document.getElementById(config.totalCountId);
        var levelSelect = document.getElementById(config.levelSelectId);
        var levelPill = document.getElementById(config.levelPillId);
        var selectAllButton = document.getElementById(config.selectAllButtonId);
        var clearSelectionButton = document.getElementById(config.clearSelectionButtonId);

        function toggleRowSelection(row) {
            if (row.isSelected()) {
                row.deselect();
            } else {
                row.select();
            }
        }

        function syncHiddenInputs(table) {
            if (!hiddenContainer) {
                return;
            }
            hiddenContainer.innerHTML = "";
            table.getSelectedData().forEach(function (row) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "student_ids";
                input.value = row.student_id;
                hiddenContainer.appendChild(input);
            });
        }

        function updateCounters(table) {
            if (selectedCounter) {
                selectedCounter.textContent = String(table.getSelectedData().length);
            }
            if (totalCounter) {
                totalCounter.textContent = String(data.length);
            }
            if (levelPill && levelSelect) {
                levelPill.textContent = levelSelect.value || "待选";
            }
        }

        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    {
                        title: "Student",
                        columns: [
                            { title: "姓名", field: "name", minWidth: 180 },
                            { title: "年级", field: "grade", hozAlign: "center", width: 120 },
                            { title: "家长手机号", field: "parent_phone", hozAlign: "center", minWidth: 150 },
                        ],
                    },
                    { title: "当前归属", field: "current_scope_text", minWidth: 320 },
                    { title: "可加入原因", field: "pool_reason_text", minWidth: 260 },
                ],
                {
                    index: "student_id",
                    selectableRows: true,
                    selectableRowsPersistence: true,
                    initialSort: [{ column: "name", dir: "asc" }],
                    rowHeader: {
                        formatter: "rowSelection",
                        titleFormatter: "rowSelection",
                        width: 56,
                        hozAlign: "center",
                        headerHozAlign: "center",
                        resizable: false,
                        frozen: true,
                        headerSort: false,
                        cellClick: function (e, cell) {
                            toggleRowSelection(cell.getRow());
                        },
                    },
                    rowClick: function (e, row) {
                        toggleRowSelection(row);
                    },
                }
            )
        );

        var searchFilter = attachSearch(table, config.searchInputId, ["name"]);
        wirePersistentState("course-student-pool", table, document.getElementById(config.tableId), config.searchInputId, searchFilter);

        table.on("tableBuilt", function () {
            updateCounters(table);
            syncHiddenInputs(table);
        });

        table.on("rowSelectionChanged", function () {
            updateCounters(table);
            syncHiddenInputs(table);
        });

        if (selectAllButton) {
            selectAllButton.addEventListener("click", function () {
                table.getRows("active").forEach(function (row) {
                    row.select();
                });
            });
        }

        if (clearSelectionButton) {
            clearSelectionButton.addEventListener("click", function () {
                table.deselectRow();
            });
        }

        if (levelSelect) {
            levelSelect.addEventListener("change", function () {
                updateCounters(table);
            });
        }

        if (form) {
            form.addEventListener("submit", function () {
                syncHiddenInputs(table);
            });
        }

        return table;
    }

    function buildHomeworkBatchStudentGrid(config) {
        var data = readJsonScript(config.dataScriptId).slice().sort(function (left, right) {
            return String(left.name || "").localeCompare(String(right.name || ""), "zh-Hans-CN");
        });
        var hiddenContainer = document.getElementById(config.hiddenContainerId);
        var form = document.getElementById(config.formId);
        var selectedCounter = document.getElementById(config.selectedCountId);
        var totalCounter = document.getElementById(config.totalCountId);
        var selectAllButton = document.getElementById(config.selectAllButtonId);
        var clearSelectionButton = document.getElementById(config.clearSelectionButtonId);

        function toggleRowSelection(row) {
            if (row.isSelected()) {
                row.deselect();
            } else {
                row.select();
            }
        }

        function syncHiddenInputs(table) {
            if (!hiddenContainer) {
                return;
            }
            hiddenContainer.innerHTML = "";
            table.getSelectedData().forEach(function (row) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "student_ids";
                input.value = row.student_id;
                hiddenContainer.appendChild(input);
            });
        }

        function updateCounters(table) {
            if (selectedCounter) {
                selectedCounter.textContent = String(table.getSelectedData().length);
            }
            if (totalCounter) {
                totalCounter.textContent = String(data.length);
            }
        }

        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    {
                        title: "Student",
                        columns: [
                            { title: "姓名", field: "name", minWidth: 180 },
                            { title: "年级", field: "grade", hozAlign: "center", width: 120 },
                            { title: "家长手机", field: "parent_phone", hozAlign: "center", minWidth: 150 },
                        ],
                    },
                    { title: "负责范围", field: "scope_text", minWidth: 320 },
                ],
                {
                    index: "student_id",
                    selectableRows: true,
                    selectableRowsPersistence: true,
                    rowHeader: {
                        formatter: "rowSelection",
                        titleFormatter: "rowSelection",
                        width: 56,
                        hozAlign: "center",
                        headerHozAlign: "center",
                        resizable: false,
                        frozen: true,
                        headerSort: false,
                        cellClick: function (e, cell) {
                            toggleRowSelection(cell.getRow());
                        },
                    },
                    rowClick: function (e, row) {
                        toggleRowSelection(row);
                    },
                }
            )
        );

        var searchFilter = attachSearch(table, config.searchInputId, ["name", "grade", "parent_phone", "scope_text"]);
        wirePersistentState("homework-batch-students", table, document.getElementById(config.tableId), config.searchInputId, searchFilter);

        table.on("tableBuilt", function () {
            var selectedIds = data.filter(function (row) { return row.selected; }).map(function (row) { return row.student_id; });
            if (selectedIds.length) {
                table.selectRow(selectedIds);
            }
            updateCounters(table);
            syncHiddenInputs(table);
        });

        table.on("rowSelectionChanged", function () {
            updateCounters(table);
            syncHiddenInputs(table);
        });

        if (selectAllButton) {
            selectAllButton.addEventListener("click", function () {
                table.getRows("active").forEach(function (row) {
                    row.select();
                });
            });
        }

        if (clearSelectionButton) {
            clearSelectionButton.addEventListener("click", function () {
                table.deselectRow();
            });
        }

        if (form) {
            form.addEventListener("submit", function () {
                syncHiddenInputs(table);
            });
        }

        return table;
    }

    function buildHomeworkImportJobGrid(config) {
        var data = readJsonScript(config.dataScriptId);
        var hiddenInput = document.getElementById(config.hiddenInputId);
        var tableElement = document.getElementById(config.tableId);

        function selectedValue() {
            return hiddenInput ? String(hiddenInput.value || "") : "";
        }

        function syncSelection(rowData) {
            if (!hiddenInput) {
                return;
            }
            hiddenInput.value = rowData ? String(rowData.import_job_id || "") : "";
        }

        function emitEvent(eventName, rowData) {
            if (!tableElement) {
                return;
            }
            tableElement.dispatchEvent(
                new CustomEvent(eventName, {
                    detail: {
                        row: rowData || null,
                    },
                })
            );
        }

        var table = new Tabulator(
            "#" + config.tableId,
            defaultOptions(
                data,
                [
                    {
                        title: "Import Job",
                        columns: [
                            { title: "文件名", field: "source_filename", minWidth: 220 },
                            { title: "创建老师", field: "teacher_display_name", minWidth: 150 },
                            { title: "来源作业", field: "assignment_title", minWidth: 180 },
                            { title: "来源课程", field: "course_title", hozAlign: "center", width: 120 },
                        ],
                    },
                    {
                        title: "Stats",
                        columns: [
                            {
                                title: "题目数",
                                field: "question_count",
                                hozAlign: "center",
                                width: 96,
                                formatter: function (cell) {
                                    var row = cell.getRow().getData();
                                    return statusPill(String(row.question_count || 0), row.question_count ? "open" : "locked");
                                },
                            },
                            { title: "创建时间", field: "created_at_text", minWidth: 170 },
                        ],
                    },
                    {
                        title: "Action",
                        columns: [
                            {
                                title: "详细",
                                field: "preview_href",
                                hozAlign: "center",
                                width: 96,
                                headerSort: false,
                                formatter: function (cell) {
                                    var row = cell.getRow().getData();
                                    return '<button class="cm-tabulator-btn" type="button" data-import-job-preview-id="' + escapeHtml(String(row.import_job_id || "")) + '">详细</button>';
                                },
                            },
                        ],
                    },
                ],
                {
                    index: "import_job_id",
                    selectableRows: 1,
                    rowHeader: {
                        formatter: "rowSelection",
                        titleFormatter: "rowSelection",
                        width: 56,
                        hozAlign: "center",
                        headerHozAlign: "center",
                        resizable: false,
                        frozen: true,
                        headerSort: false,
                        cellClick: function (e, cell) {
                            cell.getRow().select();
                        },
                    },
                    rowClick: function (e, row) {
                        if (e.target && e.target.closest("[data-import-job-preview-id]")) {
                            return;
                        }
                        row.select();
                    },
                }
            )
        );

        attachSearch(table, config.searchInputId, ["source_filename", "teacher_display_name", "teacher_username", "assignment_title", "course_title", "content_title"]);

        table.on("tableBuilt", function () {
            var presetValue = selectedValue();
            var selectedRow = data.find(function (row) {
                return String(row.import_job_id || "") === presetValue;
            }) || data.find(function (row) {
                return !!row.selected;
            });
            if (selectedRow) {
                table.selectRow([selectedRow.import_job_id]);
                syncSelection(selectedRow);
                emitEvent("codemaster:import-job-selected", selectedRow);
            }
        });

        table.on("rowSelectionChanged", function (selectedData) {
            var selectedRow = selectedData.length ? selectedData[0] : null;
            syncSelection(selectedRow);
            emitEvent("codemaster:import-job-selected", selectedRow);
        });

        if (tableElement) {
            tableElement.addEventListener("click", function (event) {
                var previewButton = event.target.closest("[data-import-job-preview-id]");
                if (!previewButton) {
                    return;
                }
                var importJobId = String(previewButton.getAttribute("data-import-job-preview-id") || "");
                var rowData = data.find(function (row) {
                    return String(row.import_job_id || "") === importJobId;
                }) || null;
                emitEvent("codemaster:import-job-preview-requested", rowData);
            });
        }

        return table;
    }

    window.CodeMasterTeacherTabulator = {
        initAssignmentStudentSelector: buildAssignmentStudentSelector,
        initAssignmentScopeSelector: buildAssignmentScopeSelector,
        initCourseStudents: buildCourseStudentsTable,
        initCourseStudentPoolGrid: buildCourseStudentPoolTable,
        initHomeworkBatchStudentGrid: buildHomeworkBatchStudentGrid,
        initHomeworkImportJobGrid: buildHomeworkImportJobGrid,
        initKnowledgeLevelSelector: buildKnowledgeLevelSelector,
        initTeacherHomeworkAssignmentSubmissionDetail: buildTeacherHomeworkAssignmentSubmissionDetailTable,
        initTeacherHomeworkSubmissionAnswerDetail: buildTeacherHomeworkSubmissionAnswerDetailTable,
        initTeacherHomeworkSubmissionDetail: buildTeacherHomeworkSubmissionDetailTable,
        initTeacherHomeworkStatsStudents: buildTeacherHomeworkStatsStudentsTable,
        initTeacherWorkbenchStudents: buildTeacherWorkbenchStudentsTable,
        initTeacherWorkbenchCourses: buildTeacherWorkbenchCoursesTable,
        initStudentAssignmentGrid: buildStudentAssignmentsTable,
        initLevelGrid: buildLevelTable,
        initKnowledgePointGrid: buildKnowledgePointTable,
        initStudentAccessGrid: buildPermissionsTable,
    };
})();
