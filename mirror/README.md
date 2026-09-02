# 前端镜像

来源: https://demo.joytest.org.cn/demo/t/xajtdxsnb_20260123/1

这是该页面加载的 Angular 前端静态资源副本，并额外抓取了登录后的主要接口返回，
因此可以在本地浏览从登录到答题界面的主要前端流程。

包含内容：

- Angular 主 bundle、runtime、polyfills、lazy chunks、样式
- 字体、图标、图片等静态资源
- 自定义 skin 资源（logo、背景、标题、`jt_custom.js`、考生须知页面等）
- 抓取到的动态 JSON：
  - `seat/session.json`
  - `seat/css/<id>.json`
  - `api/login.json`
  - `api/confirm.json`
  - `api/notice.json`
  - `api/form.json`
  - `api/response.json`
  - `api/event.json`
  - `api/response_patch.json`

## 本地预览

在仓库根目录运行：

```bash
python serve_mirror.py 8000
```

然后打开：

```text
http://localhost:8000/
```

服务会自动跳转到演示页路径：

```text
http://localhost:8000/demo/t/xajtdxsnb_20260123/1
```

本地可体验的流程包括：

1. 登录页 / 注意事项
2. 考生信息确认
3. 考生须知 / 机考操作说明（iframe）
4. 试卷说明
5. 单元说明
6. 正式答题界面（科目一单选题/多选题）
7. 暂离锁屏界面

说明：这是使用抓取到的接口返回做的本地静态模拟，用于查看前端界面和资源。
真实考试时的动态数据、提交答案、时间控制、科目切换等仍需要原始后端支持。

## 目录说明

- `client/`：Angular 应用及静态资源（原 CDN 路径已改写为本地 `/client/`）。
- `seat/session.json`、`seat/css/<id>.json`：抓取到的动态 JSON。
- `api/`：登录后流程中使用到的接口返回快照，供本地预览。
- `seat/skin/demo_0589eed14019486c/`：自定义 skin 文件。
  原路径中的 `demo:0589...` 因 Windows 文件系统不允许冒号，本地保存为 `demo_0589...`，
  `serve_mirror.py` 会自动把 URL 中的冒号映射到本地下划线目录。
