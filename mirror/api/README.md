# API 快照

这些文件是在原始演示站点上通过浏览器自动化抓取的接口返回快照，用于让本地
`serve_mirror.py` 能够在没有后端的情况下展示登录后各前端界面。

- `session.json`：`/seat/session/`
- `login.json`：`POST /seat/login/`
- `confirm.json`：`POST /seat/confirm/`（原返回为空）
- `notice.json`：`GET /seat/notice/`
- `form.json`：`GET /seat/form/`（试卷/题目结构）
- `response.json`：`GET /seat/response/?all=1`
- `event.json`：`POST /seat/event/`（原返回为空）
- `response_patch.json`：`POST /seat/response/patch/`（原返回为空）

注意：这些是演示系统的一次性快照，仅用于前端界面预览。
