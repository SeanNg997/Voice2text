# Voice2Text (Local Web)

一个本地运行的语音转写网页工具，支持拖拽上传音频、实时查看转写状态，并下载生成的 Markdown 文件。

## 功能

- 拖拽或按钮导入音频文件
- 后端异步转写，前端轮询展示状态日志
- 转写结果自动生成为 `.md` 文件并提供下载

## 环境要求

- Python 3.10+
- pip

## 安装依赖

```bash
pip3 install -r requirements.txt
```

## 配置 API（环境变量）

本项目不会在代码中保存密钥，请在运行前配置以下环境变量：

- `XF_APPID`
- `XF_API_KEY`
- `XF_API_SECRET`

示例（macOS / Linux）：

```bash
export XF_APPID="your_appid"
export XF_API_KEY="your_api_key"
export XF_API_SECRET="your_api_secret"
```

## 启动

```bash
python3 voice2text.py
```

浏览器访问：`http://127.0.0.1:8000`

## 目录说明

- `voice2text.py`: Flask 后端 + 转写核心逻辑
- `templates/index.html`: 前端页面
- `uploads/`: 临时上传目录（任务结束后会删除临时音频）
- `outputs/`: 转写结果目录（Markdown）

## 注意

- `outputs/` 目录已在 `.gitignore` 中忽略，不会被上传到仓库。
- 如果缺少环境变量，服务在转写时会提示缺少对应配置项。
