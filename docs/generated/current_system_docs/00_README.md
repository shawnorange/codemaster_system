# CodeMaster System 当前系统文档包

## 1. 文档包说明
本目录包含三份核心文档：
- 功能清单与功能规格说明书
- 数据库字典
- 接口文档

## 2. 生成时间
2026-05-08 15:09:47 CST

## 3. 生成依据
- manage.py
- requirements.txt
- codemaster_system/settings.py
- codemaster_system/urls.py
- entry/urls.py
- entry/models.py
- entry/auth.py
- entry/views.py
- entry/portal_context.py
- entry/homework_online.py
- entry/homework_batch.py
- entry/content_visibility.py
- entry/templates/entry/
- entry/static/entry/
- entry/tests/
- entry/management/commands/

## 4. 文件清单
- `00_README.md`
- `01_function_spec/功能清单与功能规格说明书.md`
- `01_function_spec/功能矩阵.csv`
- `01_function_spec/角色权限矩阵.csv`
- `01_function_spec/页面路由清单.csv`
- `02_database_dictionary/er_diagram.mmd`
- `02_database_dictionary/数据库关系说明.csv`
- `02_database_dictionary/数据库字典.md`
- `02_database_dictionary/数据库字段明细.csv`
- `02_database_dictionary/数据库表清单.csv`
- `03_api_docs/openapi.yaml`
- `03_api_docs/postman_collection.json`
- `03_api_docs/route_catalog.csv`
- `03_api_docs/接口文档.md`
- `codemaster_current_system_docs.zip`

## 5. 如何使用
- 新功能开发前先看 `01_function_spec/功能清单与功能规格说明书.md`。
- 涉及数据结构先看 `02_database_dictionary/数据库字典.md`。
- 涉及路由/API 先看 `03_api_docs/接口文档.md`。
- 涉及权限先看 `01_function_spec/角色权限矩阵.csv`。
- 涉及作业链路先看数据库字典中的作业主链路说明和接口文档中的作业路由。

## 6. 安全说明
- 文档不包含真实密钥。
- 文档不包含真实密码或密码哈希。
- 示例数据已脱敏，例如 张三 / 138****0000 / student_demo。
- 未读取或输出 `.env` 内容。
- 未读取或输出 media 文件内容。
- 管理命令中存在导入账号默认密码相关逻辑，本文档仅说明风险，不披露具体密码值。

## 7. 已知不确定项
- 动态 URL 和动态模板分支只能根据代码静态推断。
- 动态 context 字段未逐项展开到模板变量级别。
- JavaScript 触发的请求已通过静态搜索整理，但运行时分支仍需浏览器验证。
- 外部 API 真实返回、错误码和服务可用性未在线调用验证。
- 未连接生产数据库，无法确认真实数据规模、脏数据和历史兼容样本。
- 微信小程序线上 appid/secret、手机号绑定范围和发布配置未确认。

## 8. 生成检查说明
- `./.venv/bin/python manage.py check` 通过，输出无系统检查问题。
- `./.venv/bin/python manage.py makemigrations --check --dry-run` 未生成迁移；命令在沙箱中尝试检查数据库迁移一致性时出现 PostgreSQL 连接受限警告，但最终提示 No changes detected。
- 未执行 migrate、collectstatic、pg_dump、pg_restore。
- xlsx 生成状态：未生成；当前 Python 环境缺少 openpyxl，按要求保留 CSV fallback，未安装依赖。
