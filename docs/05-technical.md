# 序幕技术架构与运行说明

更新日期：2026-10-25。序幕使用静态网页、Python 标准库本地服务、SQLite 和百炼异步视频接口。无需前端构建或额外后端运行依赖，当前只用于本机，不应直接暴露到公网。

## 模块与数据边界

浏览器访问 dist 页面及同源 API；server.py 校验输入、建立任务并由后台调用供应商。供应商返回结果后，服务端将视频下载到 .studio/videos，再通过会话校验地址提供给浏览器。API Key、供应商签名地址和上游任务号不返回前端。

| 路径 | 作用 | 是否同步 GitHub |
| --- | --- | --- |
| dist | 页面、脚本、样式、字体、内置参考图和云海循环 | 是 |
| server.py 与 tests | 本地服务和离线接口测试 | 是 |
| video-production | 背景素材制作工具、原始公共云海素材及测试 | 是，排除 .state |
| docs | 产品、技术及验证文档 | 是 |
| .env.local | 私有密钥及专属接口配置 | 否 |
| .studio | 会话任务、数据库、结果视频与备份 | 否 |
| preview 与 video-production/.state | 开发截图、运行记录、上游任务状态 | 否 |

## 本地启动

使用 Python 3.9 及以上。macOS 可双击“启动序幕.command”，或在项目目录运行以下命令。

```sh
cp .env.example .env.local
chmod 600 .env.local
# 在本机编辑 .env.local，填入自己的北京地域配置
python3 server.py
```

访问 http://127.0.0.1:4173 。若服务已启动，直接访问，不要重复启动。直接打开 dist/index.html 或普通静态预览只能展示页面，无法真实生成。当前服务使用 fcntl 文件锁，其他操作系统需要适配后测试。

私有配置包含 DASHSCOPE_API_KEY 和 DASHSCOPE_API_HOST。使用控制台给出的完整 Key 与对应北京地域业务空间主机名，不能截短带点号的 Key，也不能混用地域。仓库的 .env.example 仅保留空值。

## 生成配置与估价

| 模式 | 固定模型 | 参数 | 北京公开标价 |
| --- | --- | --- | --- |
| 自由文生 | wan2.6-t2v | 720P，2–15 整数秒，单镜头，三种画幅 | 0.60 元每秒 |
| 内置首帧 | wan2.6-i2v-flash | 720P，2–15 整数秒，单镜头，有声，16:9 | 0.30 元每秒 |

估算＝有效时长×对应单价，例如 8 秒文生为 4.80 元，8 秒首帧为 2.40 元。此为 2026-10-25 核验的北京公开原价，不代表实时余额或实扣；额度、优惠及实际账单以账号为准。[百炼价格](https://help.aliyun.com/zh/model-studio/model-pricing)

首帧模式将允许列表图片编码为 data:image/webp;base64，放入 input.img_url，同时设置 audio=true、prompt_extend=true、shot_type=single。纯文字模式只传提示词和合法尺寸。相关接口见[文生参考](https://help.aliyun.com/zh/model-studio/legacy-wan-text-to-video-api-reference)与[首帧参考](https://help.aliyun.com/zh/model-studio/legacy-image-to-video-api-reference/)。

PRELUDE_DAILY_LIMIT_CENTS 默认 3000，合法范围 300–30000，按北京时间自然日计算已预留标价；明确失败和拒绝不占用。全局仅一条活动任务，UNCERTAIN 也阻止新提交。预算不读取账号余额，不会充值。

## 本地 API

| 方法与路径 | 用途 |
| --- | --- |
| GET /api/bootstrap | 返回服务能力、CSRF、轮询间隔、作品及最近删除 |
| GET /api/generations | 当前会话作品；deleted=1 查看最近删除 |
| POST /api/generations | 创建任务，成功返回 202 |
| GET /api/generations/{id} | 查询当前会话任务 |
| GET /api/generations/{id}/video | 播放；download=1 下载，支持 Range |
| POST /api/generations/{id}/rename | 保存 title |
| POST /api/generations/{id}/favorite | 更新 favorite 布尔值 |
| POST /api/generations/{id}/trash | 移入最近删除 |
| POST /api/generations/{id}/restore | 恢复作品 |

写接口为 application/json，并带 X-Prelude-CSRF 及同源会话 Cookie。创建示例仅示意字段，不应直接当作真实付费请求执行。

```json
{
  "prompt": "镜头缓慢向前，保持雨夜街道的光线与构图",
  "aspect": "16:9",
  "duration": 8,
  "request_id": "example-request-unique-0001",
  "reference_preset": "city"
}
```

不带 reference_preset 时为文生。相同 request_id 的参数必须完全相同，任务模式随记录持久化；供应商模型由服务端决定，不接受前端自选。错误返回 error 和 code，前端依据状态解释，不能遇错就重新 POST。

## 持久化与故障恢复

SQLite 保存本地任务、会话归属摘要、输入参数、估价、状态、模型、参考及管理字段。刷新或重启继续处理原任务；若提交是否被上游接受不明，进入 UNCERTAIN 并人工核对，不自动重发。获得上游任务号后只查询该任务；下载失败仅重新取原结果。

视频下载仅允许指定阿里域名的 HTTPS 地址，不转发密钥、不跟随跳转，限制 100MiB 并检查 MP4 文件头。删除只加标记，不擦除文件。备份恢复时数据库和视频应成套保留；备份仍含私人记录，不进入代码仓库。

## 验证与部署前提

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s video-production -p 'test_*.py'
```

以上测试使用模拟供应商或本地 HTTP，不调用百炼、不产生生成费用。背景制作工具的 submit 才会真实提交，必须独立确认预算，不能纳入自动测试。

公网阶段需要正式身份、会话恢复、生产 Web 服务、任务队列、持久存储、配额计费、访问控制、监控与数据保留策略。当前静态网页上传到托管站点不能自动获得这些能力；本次 GitHub 同步也不等于部署。
