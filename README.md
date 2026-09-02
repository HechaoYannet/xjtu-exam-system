# 本地考试后端管理系统

基于本仓库中的本地考试机前端镜像（`mirror/`），提供一套不依赖数据库的考试后端管理系统。
所有业务数据以明文 JSON 文件保存在 `data/` 目录，便于查看、备份和用 CLI 管理。

---

## 1. 系统组成

| 模块 | 说明 | 地址 |
| --- | --- | --- |
| 后端服务 | `exam_backend.py`，提供 API、管理后台、考试机接口 | `http://localhost:8000` |
| 管理后台 | 管理员使用的完整后台 | `http://localhost:8000/admin` |
| 考试机客户端 | 考生/考试机使用的答题客户端 | `http://localhost:8000/exam-client` |
| 本地前端镜像 | 原始考试机前端静态资源 | `http://localhost:8000/` |
| CLI | 命令行管理工具 | `python exam_backend.py ...` |

---

## 2. 快速启动

```bash
# 进入项目目录
cd xjtu-exam-system

# 启动后端（默认端口 8000）
python exam_backend.py server --port 8000

# 导入示例数据（可选，包含考试、科目、考生、试题）
python exam_backend.py seed-demo
```

启动后访问：

- 管理后台：<http://localhost:8000/admin>
- 考试机客户端：<http://localhost:8000/exam-client>

---

## 3. 管理后台使用说明

### 3.1 总览监控

后台首页显示：

- 考生总数
- 试题总数
- 考试总数
- 考试记录数量
- 当前系统模式
- 当前进行中的考试
- 最近事件

页面每 5 秒自动刷新，也可以点击右上角“刷新”。

### 3.2 考生管理

1. 点击左侧“考生管理”。
2. 点击“新增考生”。
3. 填写：
   - 准考证号：考试机登录时输入的号码
   - 姓名
   - 性别
   - 身份证号
   - 座位号/机号
   - 参加考试
4. 保存后，考生会出现在列表中。
5. 可通过搜索框按准考证号、姓名、身份证号筛选。
6. 可编辑或删除考生。

### 3.3 试题管理

1. 点击左侧“试题管理”。
2. 点击“新增试题”。
3. 填写：
   - 题型：单选、多选、判断、填空、简答
   - 题干
   - 选项 JSON
   - 答案
   - 分值
   - 分组/科目
4. 保存后可搜索、编辑、删除、启用/停用。

### 3.4 考试管理

推荐完整流程：

1. 点击左侧“考试管理”。
2. 点击“创建考试”。
3. 填写考试名称、时长、模式、是否自动开考、考试说明。
4. 可同时填写“初始科目”，也可创建后再添加科目。
5. 在考试列表点击“科目”，为考试添加多个科目。
6. 每个科目需要配置：
   - 科目名称
   - 答题时长
   - 引导时长
   - 试题 ID
   - 是否允许提前交卷
7. 点击“分配考生”，选择本场考试的考生。
8. 点击“准备考试”，进入准备阶段。
9. 考试机客户端登录并完成考生信息确认。
10. 点击“开始考试”，考试机自动进入答题。
11. 考试过程中可点击“结束”提前终止整场考试。

### 3.5 考试记录

- 查看每个考生的考试记录
- 查看开始时间、结束时间、得分、答题数
- 查看答题详情
- 可手动为单个考生开考/结束

### 3.6 数据管理

- 切换系统模式：
  - 模拟模式：默认，支持多场并发、自由考试
  - 标准模式：全局同时一场考试，后台统一控制
- 导出全部数据为 JSON
- 导入备份数据
- 查看数据文件位置

### 3.7 事件日志

记录考生登录、交卷、引导、科目切换、考试结束等事件，便于监考回溯。

---

## 4. 考试机客户端使用说明

### 4.1 登录

1. 在考试机浏览器打开：`http://<服务器地址>:8000/exam-client`
2. 输入准考证号。
3. 点击“登录 / 开始”。

可能出现的提示：

- `准考证号不存在`：该考号未导入后台。
- `未在进行考试`：当前没有可参加的考试，或尚未准备/开始。

### 4.2 考生信息确认

- 登录后，如果是准备阶段，客户端会显示考试标题、考生姓名、座位号/机号。
- 核对信息后点击“我已确认，进入等待”。
- 然后等待管理员点击“开始考试”。

### 4.3 答题界面

- 左侧为答题区。
- 右侧为答题卡/侧栏。
- 倒计时以后端返回的 `server_time` 为准。
- 选择答案后自动保存到后端。
- 如果后端允许提前交卷，会显示“提前交卷”按钮。

### 4.4 科目引导

- 多科目考试中，科目一时间到会自动收卷。
- 客户端自动进入下一科目引导界面，并显示引导倒计时（默认 10 分钟）。
- 点击“我已阅读引导”后，等待后端引导结束信号。
- 引导结束后自动进入答题。
- 如果引导未完成，考生不能进入答题，但考试计时已经开始。

### 4.5 考试结束

- 所有科目结束后，客户端显示“考试结束”。
- 可点击“退出登录”返回登录页面。

### 4.6 多人多机

- 每一台考试机/浏览器都可以独立打开客户端。
- 每个准考证号独立创建考试会话。
- 多位考生可同时登录、同时答题，互不干扰。

---

## 5. CLI 使用示例

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
python exam_backend.py exam prepare --id <exam_id>
python exam_backend.py exam start --id <exam_id>
python exam_backend.py exam stop --id <exam_id>
python exam_backend.py exam auto --id <exam_id> --on

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

---

## 6. 考试生命周期

1. 创建考试
2. 添加考试科目
3. 为科目配置试题
4. 分配考生
5. 管理后台点击“准备考试”
6. 考试机客户端登录、核验信息、进入等待
7. 管理后台点击“开始考试”
8. 考试机进入答题，后端开始倒计时
9. 科目时间到自动收卷
10. 进入下一科目引导
11. 引导结束自动进入答题
12. 全部科目结束，显示考试结束

所有倒计时均以后端系统时间为准。

---

## 7. 系统模式

### 模拟模式（默认）

- 可同时进行多场考试。
- 考试机自由登录、自由考试。
- 按照考试列表顺序匹配准考证号。
- 存在倒计时，但不依赖后台统一开考按钮。

### 标准模式

- 全局同一时间只允许一场考试。
- 必须由后台统一管理：
  - 准备考试
  - 开始考试
- 准考证号只在本场考试中检索。
- 后端不向客户端下发下一科信息，由后端统一发信号。

可在管理后台“数据管理”或通过 API/CLI 切换。

---

## 8. 数据文件

| 文件 | 内容 |
| --- | --- |
| `data/candidates.json` | 考生/准考证号 |
| `data/questions.json` | 试题库 |
| `data/exams.json` | 考试、科目、配置、状态 |
| `data/sessions.json` | 考生考试记录、答案、状态 |
| `data/events.json` | 事件日志 |
| `data/settings.json` | 系统模式（模拟/标准） |

所有文件均为可直接阅读的 JSON 明文，未使用数据库。

---

## 9. 上线部署建议：Vercel 还是 Railway？

**推荐：Railway，更合适本题系统。**

原因：

| 对比项 | Vercel | Railway |
| --- | --- | --- |
| 定位 | 前端/Serverless/静态站 | 长期运行容器/服务 |
| 是否适合 Python 长驻后端 | 不适合 | 适合 |
| 文件持久化 | Serverless 文件系统通常是临时的 | 可挂载 Volume/持久化磁盘 |
| 后台计时/状态推进 | 不适合常驻进程 | 适合常驻进程 |
| 多人在线考试 | 需要额外架构，困难 | 可直接运行后端服务 |
| 本项目适配度 | 低 | 高 |

本项目是一个需要**常驻运行、本地 JSON 持久化、后端统一计时、考试机轮询状态**的 Python 服务，不是普通静态站点。

- Vercel 更适合托管静态前端、文档或 Serverless API，但很难长期保存 `data/` 下的 JSON 文件，也不适合后台计时线程。
- Railway 可以运行完整的 Python 进程，保持文件系统，提供固定端口，适合小访问量正常使用。

### Railway 上线要点

1. 在 Railway 创建新项目。
2. 选择从 GitHub 导入仓库。
3. 配置启动命令：
   ```bash
   python exam_backend.py server --port $PORT
   ```
4. 挂载持久化 Volume 到 `data/` 目录。
5. 设置公开域名。
6. 考试机客户端访问：
   ```text
   https://<你的域名>/exam-client
   ```
7. 管理后台访问：
   ```text
   https://<你的域名>/admin
   ```

### Vercel 的适用场景

如果只想部署静态说明页或纯前端演示，Vercel 很合适；本项目已经通过 GitHub Pages 部署了静态说明页。

---

## 10. HTTP 接口简表

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

考试机接口（`/seat`，兼容本地前端镜像）：

- `GET /seat/session/`
- `POST /seat/login/`
- `POST /seat/confirm/`
- `GET /seat/notice/`、`GET /seat/form/`
- `GET /seat/response/`、`POST /seat/response/patch/`
- `POST /seat/event/`、`POST /seat/end/`、`GET /seat/score/`
- 静态皮肤、资源等由同一服务提供。
