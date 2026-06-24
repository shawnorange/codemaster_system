# Scratch 编程平台服务

CodeMaster 已内置 Scratch/TurboWarp 编辑器源码，目录为 `services/scratch-editor`。模考编程题通过 `DASHIMA_SCRATCH_EDITOR_URL` 打开编辑器，并把学生作品实时保存、提交到 CodeMaster 后端。

## 本地开发

启动 Scratch 服务：

```bash
docker compose up scratch-editor
```

默认地址：

```text
http://127.0.0.1:8601/editor.html
```

CodeMaster 本地编排已默认设置：

```text
DASHIMA_SCRATCH_EDITOR_URL=http://127.0.0.1:8601/editor.html
```

## 正式部署

正式编排会构建 `scratch-editor` 服务，并通过 nginx 暴露：

```text
/scratch/editor.html
```

如需改成独立域名，例如 `https://scratch.example.com/editor.html`，在服务器 `.env` 中设置：

```text
DASHIMA_SCRATCH_EDITOR_URL=https://scratch.example.com/editor.html
```

## 保存边界

学生作品文件仍保存在 CodeMaster 后端的 `ProgrammingSubmission` 和媒体目录中。Scratch 编辑器只负责编辑、实时保存和提交，不单独保存学生数据。

不要提交以下目录：

```text
services/scratch-editor/node_modules
services/scratch-editor/build
services/scratch-editor/dist
services/scratch-editor/translations
services/scratch-editor/static/microbit
```
