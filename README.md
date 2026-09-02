# 本地考试后端管理系统

基于本仓库中的本地考试机前端镜像（`mirror/`），提供一个不依赖数据库的后端考试管理系统。
所有业务数据以明文 JSON 文件保存在 `data/` 目录，便于查看、备份和用 CLI 管理。

## 功能

- **考生管理后台**：按准考证号管理考生信息（姓名、性别、身份证、座位号、参加考试等）。
- **试题管理后台**：管理单选、多选等题型，包含题干、选项、答案、分值、科目/分组。
- **考试管理后台**：
  - 统一考试：一场考试统一开始、结束。
  - 单独启动：可为单个考生手动启动/结束考试记录。
  - 自动开考开关：开启后，考试机输入准考证号即可自动开始答题。
- **数据管理系统**：
  - 明文 JSON 存储（`candidates.json`、`questions.json`、`exams.json`、`sessions.json`、`events.json`）。
  - HTTP 管理 API 与网页后台。
  - 管理 CLI：考生/试题/考试/考试记录/数据导入导出。

## 考试生命周期

- **创建考试**：创建考试、添加科目、为每个科目选择试题、分配考生。
- **准备考试**：管理员点击“准备考试”后，考试机前端显示考试信息并允许考生登录核验、阅读须知，进入等待开始。
- **开始考试**：管理员点击“开始考试”，后端统一发出开考信号，考试机进入答题并以后端时间倒计时。
- 多科目考试：科目一时间到自动收卷，进入下一科目引导；引导倒计时结束后由后端信号切换到答题。
- 所有倒计时均以后端 `server_time` 为准。

## 管理后台

后台地址：`http://127.0.0.1:8000/admin`

包含：
- 总览监控（状态卡片、自动刷新、最近事件、运行建议）
- 考生管理（搜索、筛选、新增/编辑/删除、分配考试）
- 试题管理（搜索、题型筛选、新增/编辑/删除）
- 考试管理（创建、开始/结束、自动开考开关、分配考生）
- 考试记录（查看答题详情、手动开考/结束、得分）
- 数据管理（导出/导入 JSON、数据文件说明）
- 事件日志

## 考试机客户端

新增加了一个可运行的前端考试机页面，用于演示完整生命周期：

```text
http://127.0.0.1:8000/exam-client
```

该页面支持：
- 准考证号登录，错误显示“准考证号不存在”，无考试显示“未在进行考试”
- 右侧答题卡/侧栏
- 以后端时间为准的倒计时
- 科目引导倒计时、等待开始、自动进入答题
- 后端允许提前交卷时显示提前交卷
- 模拟/标准模式切换
- 全部科目结束后显示考试结束

## 全流程演示

```bash
# 1. 导入示例：包含考试、两个科目、考生
python exam_backend.py seed-demo

# 2. 启动后端
python exam_backend.py server --port 8000

# 3. 管理后台准备并开始考试
#    http://127.0.0.1:8000/admin

# 4. 打开考试机客户端
#    http://127.0.0.1:8000/exam-client
#    使用示例准考证号 1234CS 登录
```

## 快速开始

```bash
# 启动后端（同时提供管理后台和考试机前端）
python exam_backend.py server --port 8000

# 管理后台
open http://127.0.0.1:8000/admin

# 考试机前端
open http://127.0.0.1:8000/
```

首次使用可导入前端镜像中的示例试题：

```bash
python exam_backend.py seed-demo
```

## CLI 示例

```bash
# 考生管理
python exam_backend.py candidate add --ticket 20260001 --name 张三 --gender 男 --exam <exam_id>
python exam_backend.py candidate list
python exam_backend.py candidate update --ticket 20260001 --name 张三三
python exam_backend.py candidate remove --ticket 20260001

# 试题管理
python exam_backend.py question add --type sc --stem "1+1=?" \
  --option "A=1" --option "B=2" --option "C=3" --answer B --score 2 --subject 数学 --group 单选题
python exam_backend.py question list
python exam_backend.py question remove --id <question_id>

# 考试管理
python exam_backend.py exam create --name "期中考试" --duration 20 --mode unified --auto
python exam_backend.py exam assign --id <exam_id> --tickets 20260001,20260002
python exam_backend.py exam start --id <exam_id>
python exam_backend.py exam auto --id <exam_id> --on
python exam_backend.py exam stop --id <exam_id>

# 单独开考
python exam_backend.py session start --ticket 20260001 --exam <exam_id>
python exam_backend.py session stop --ticket 20260001 --exam <exam_id>

# 系统模式
python exam_backend.py settings get
python exam_backend.py settings set --mode standard

# 数据备份/恢复
python exam_backend.py data export --output backup.json
python exam_backend.py data import --input backup.json
```

## 系统模式

- **模拟模式（默认）**：可多场考试同时进行，考生按顺序匹配考试，自由登录、自由考试，仍受后端倒计时控制。
- **标准模式**：全局同时只允许一场考试，必须由“准备考试 → 开始考试”统一控制，考号只在本场考试中检索。
- 可在管理后台“数据管理”中切换，或通过 `GET/POST /api/settings` 管理。

## 数据文件

| 文件 | 内容 |
| --- | --- |
| `data/candidates.json` | 考生/准考证号 |
| `data/questions.json` | 试题库 |
| `data/exams.json` | 考试配置与状态 |
| `data/sessions.json` | 考试记录、答案、状态 |
| `data/events.json` | 考试事件日志 |
| `data/settings.json` | 系统模式（模拟/标准） |

所有文件均为可直接阅读的 JSON 明文，未使用数据库。

## HTTP 接口简表

管理后台/API（`/api`）：

- `GET/POST /api/candidates`，`GET/PUT/PATCH/DELETE /api/candidates/<准考证号>`
- `GET/POST /api/questions`，`GET/PUT/PATCH/DELETE /api/questions/<试题ID>`
- `GET/POST /api/exams`，`GET/PUT/PATCH/DELETE /api/exams/<考试ID>`
- `POST /api/exams/<考试ID>/prepare|start|stop|auto|assign|unassign`
- `GET/POST /api/exams/<考试ID>/subjects`，`PUT/DELETE /api/exams/<考试ID>/subjects/<科目ID>`
- `GET/PUT /api/settings`
- `POST /api/exam-client/login`，`GET /api/exam-client/state`
- `POST /api/exam-client/answer`，`POST /api/exam-client/early-submit`，`POST /api/exam-client/guide-complete`
- `GET /api/sessions`，`GET /api/sessions/<准考证号>`，`POST /api/sessions/start|stop`
- `GET /api/overview`
- `GET /api/events`
- `GET /api/data/export`，`POST /api/data/import`

考试机接口（`/seat`，供本地前端使用）：

- `GET /seat/session/`
- `POST /seat/login/`
- `POST /seat/confirm/`
- `GET /seat/notice/`、`GET /seat/form/`
- `GET /seat/response/`、`POST /seat/response/patch/`
- `POST /seat/event/`、`POST /seat/end/`、`GET /seat/score/`
- 静态皮肤、资源等由同一服务提供。
