# 序幕技术架构与运行说明

更新日期：2026-10-29。序幕使用静态网页、Python 服务、Pillow 图像校验、SQLite 和百炼异步视频接口，已补齐邀请身份、个人额度及单机内测部署适配。无需前端构建，当前实际服务仍只在本机；Linux 生产模板尚未上机验收。

## 生成参数冻结与质量对照准备

`generation_spec.py` 只处理输入快照与规范 JSON 字节，不读取密钥或调用网络。创建任务在同一事务中写入 `request_spec`、`request_fingerprint` 和 `request_recorded_at`；记录原始文字、去除首尾空白后实际提交的文字与各自 SHA256、模型、全部显式参数及实际首帧的格式、长度和 SHA256。图片字节、base64、认证头和签名 URL 不进入快照。`request_started_at` 记录尝试开始，供应商受理时间仍使用原有 `submitted_at`，两者不能互相替代。

worker 与百炼适配器均按冻结记录构建请求，校验作品字段、图片字节和请求指纹；不在执行时重新套用后来变更的默认值。实际发出的 JSON 字节与计算指纹的字节相同。记录或图片不匹配时在模型 POST 前拒绝，数据库触发器禁止改写已存在的快照、指纹和记录时间。可正常修改作品名称、收藏与删除状态。

迁移仅增加可空列，不制造旧参数。历史 `generation_record.state=legacy_unrecorded`；新任务为 `recorded`。详情默认收起记录，历史作品明确缺少完整设置；普通创作未显式设置 seed 时不伪造具体数值。wan2.6 不返回内部扩写后的实际文本，快照明确记录未返回。依据为[官方文生 API](https://help.aliyun.com/zh/model-studio/legacy-wan-text-to-video-api-reference)。

`quality_plan.py` 是独立离线准备器，只读原文作品，输出私有 JSON 和只读审阅 HTML，没有生成入口，也不修改普通创作的随机种子。拟比较两条 15 秒纯文任务，共用 wan2.6-t2v、16:9/1280×720、扩写开启、单镜头及 seed=20261029；唯一设计因素为主体转身或相机环绕。扩写仍是不可观察因素，同 seed 不保证一致结果。北京区 720P 公开标价 0.60 元/秒，共 18 元；额度未重新核对、预算未获新授权，保持 PREPARED。定价核对日期 2026-10-29，见[官方模型页](https://help.aliyun.com/zh/model-studio/wan2-6-t2v)。

## 模块与数据边界

浏览器访问 dist 页面及同源 API；server.py 校验输入、建立任务并由后台调用供应商。供应商返回结果后，服务端将视频下载到 .studio/videos，再通过会话校验地址提供给浏览器。API Key、供应商签名地址和上游任务号不返回前端。

| 路径 | 作用 | 是否同步 GitHub |
| --- | --- | --- |
| dist | 页面、脚本、样式、字体、内置参考图和云海循环 | 是 |
| server.py 与 tests | 本地服务和离线接口测试 | 是 |
| video-production | 背景素材制作工具、原始公共云海素材及测试 | 是，排除 .state |
| docs | 产品、技术及验证文档 | 是 |
| examples | 原创实测首帧与实际提示词 | 是 |
| .venv | 本机 Python 运行环境 | 否 |
| .env.local | 私有密钥及专属接口配置 | 否 |
| .studio | 会话任务、数据库、私有参考图、结果视频与备份 | 否 |
| preview 与 video-production/.state | 开发截图、运行记录、上游任务状态 | 否 |

## 本地启动

使用 Python 3.10 及以上，安装 requirements.txt 中的 Pillow。macOS 启动脚本优先使用项目 .venv，环境准备后可双击；首次在项目目录运行以下命令。

```sh
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements.txt
cp .env.example .env.local
chmod 600 .env.local
# 在本机编辑 .env.local，填入自己的北京地域配置
.venv/bin/python3 server.py
```

访问 http://127.0.0.1:4173 。若服务已启动，直接访问，不要重复启动。直接打开 dist/index.html 或普通静态预览只能展示页面，无法真实生成。当前服务使用 fcntl 文件锁，其他操作系统需要适配后测试。

私有配置包含 DASHSCOPE_API_KEY 和 DASHSCOPE_API_HOST。使用控制台给出的完整 Key 与对应北京地域业务空间主机名，不能截短带点号的 Key，也不能混用地域。仓库的 .env.example 仅保留空值。

## 生成配置与估价

| 模式 | 固定模型 | 参数 | 北京公开标价 |
| --- | --- | --- | --- |
| 自由文生 | wan2.6-t2v | 720P，2–15 整数秒，单镜头，三种画幅 | 0.60 元每秒 |
| 内置首帧 | wan2.6-i2v-flash | 720P，2–15 整数秒，单镜头，有声，16:9 | 0.30 元每秒 |
| 自传首帧 | wan2.6-i2v-flash | 720P，2–15 整数秒，单镜头，有声，画幅随图片 | 0.30 元每秒 |

估算＝有效时长×对应单价，例如 8 秒文生为 4.80 元，8 秒首帧为 2.40 元。此为 2026-10-25 核验的北京公开原价，不代表实时余额或实扣；额度、优惠及实际账单以账号为准。[百炼价格](https://help.aliyun.com/zh/model-studio/model-pricing)

首帧模式将内置允许列表图片编码为 data:image/webp;base64，自传图片则使用服务端标准化后的 data:image/jpeg;base64，放入 input.img_url，同时设置 audio=true、prompt_extend=true、shot_type=single。纯文字模式只传提示词和合法尺寸。相关接口见[文生参考](https://help.aliyun.com/zh/model-studio/legacy-wan-text-to-video-api-reference)与[首帧参考](https://help.aliyun.com/zh/model-studio/legacy-image-to-video-api-reference/)。

自传图片只接受实际可解码的单帧 JPEG、PNG、WebP，最大 10MiB，短边至少 240、长边不超过 8000，总像素不超过 2400 万，长宽比在 1:4 至 4:1 之间。服务端核对 MIME 与实际格式，应用 EXIF 方向，缩至最长边 2560，透明区域铺深蓝底，移除元数据后重新编码 JPEG。每会话最多 100 张、200MiB，规范化内容按会话去重。图片保存于权限受限的 .studio/references，上传本身不调用供应商。

PRELUDE_DAILY_LIMIT_CENTS 默认 3000，合法范围 300–30000，按北京时间自然日计算已预留标价；明确失败和拒绝不占用。全局仅一条活动任务，UNCERTAIN 也阻止新提交。预算不读取账号余额，不会充值。

## 内测身份与个人额度

`access.py` 管理 `accounts`、`invitations`、`account_sessions`、`access_attempts` 表。通行名经 NFKC 和大小写折叠后唯一，密码使用每账户独立随机盐及 PBKDF2-HMAC-SHA256 600,000 轮，不保存明文。参数参考 [OWASP 密码存储说明](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。会话仅保存高熵 token 的 SHA256 摘要，有效 30 天，登录、开通与退出轮换 HttpOnly/SameSite Cookie；退出立即撤销当前设备会话。

服务默认要求通行证才能写入或生成；匿名旧作品仍可用原会话读取。首次开通在同一 SQLite 事务中消费单次邀请、创建身份并绑定本浏览器的旧任务与参考图，文件不搬移。已有账户登录不自动合并其他匿名记录。跨设备登录找回的是同一服务器上的已绑定作品，不包含本地草稿和镜头方案。尚未部署，因此本机 URL 本身无法跨电脑使用。

每个邀请默认发放 30 秒累计总额度，有效期默认 7 天、只可开通一次。个人用量是开通后的任务时长总和，排除明确 FAILED/REJECTED；历史迁入任务不占用新额度。SUCCEEDED、UNCERTAIN、UNAVAILABLE 和最近删除中的任务继续计入。创建任务在 BEGIN IMMEDIATE 内同时检查幂等、个人额度、全局活动任务及每日总预算，不以客户端显示值作为授权依据。默认额度可由创建邀请的本机管理员设置，不能由前端传入或修改。

登录按通行名、来源地址摘要限速，15 分钟窗口分别最多 6 次、40 次，先持久预留再计算密码，成功清除通行名失败桶，地址桶仍保留。重启不会解除限速。当前未接反向代理，不信任客户端传来的 X-Forwarded-For。

```sh
.venv/bin/python3 access_admin.py invite --seconds 30 --days 7
.venv/bin/python3 access_admin.py list
.venv/bin/python3 access_admin.py revoke 邀请编号
.venv/bin/python3 access_admin.py allowance 通行名 30
.venv/bin/python3 access_admin.py reset-password 通行名
```

邀请写入 `.studio/invitations/编号.txt`（0600），不显示在终端或返回公开接口。额度命令设置累计总额度，不重置已用量或改变全局预算。密码重设使用终端隐藏输入并撤销该账户全部设备会话。管理工具不启动后台生成线程。`PRELUDE_INVITE_ONLY` 是进程环境变量，只允许 0/1，默认 1；0 仅用于保留匿名的本地开发验证，不用于外部内测。

## 本地 API

| 方法与路径 | 用途 |
| --- | --- |
| GET /api/bootstrap | 返回服务能力、CSRF、轮询间隔、作品及最近删除 |
| POST /api/access/activate | 一次性邀请开通通行证，绑定当前匿名作品并轮换会话 |
| POST /api/access/login | 登录原通行证，在同一服务器找回作品 |
| POST /api/access/logout | 撤销当前设备会话并轮换匿名 Cookie |
| POST /api/references | 上传二进制图片，返回当前会话的参考元数据 |
| GET /api/references/{id} | 查询当前会话参考图元数据 |
| GET /api/references/{id}/image | 获取标准化私有 JPEG |
| GET /api/generations | 当前会话作品；deleted=1 查看最近删除 |
| POST /api/generations | 创建任务，成功返回 202 |
| GET /api/generations/{id} | 查询当前会话任务 |
| GET /api/generations/{id}/video | 播放；download=1 下载，支持 Range |
| POST /api/generations/{id}/rename | 保存 title |
| POST /api/generations/{id}/favorite | 更新 favorite 布尔值 |
| POST /api/generations/{id}/trash | 移入最近删除 |
| POST /api/generations/{id}/restore | 恢复作品 |

写接口均校验 X-Prelude-CSRF、同源与会话归属。除图片上传外使用 application/json；上传请求 Content-Type 为 image/jpeg、image/png 或 image/webp，X-Prelude-File-Name 为 URL 编码文件名，正文为原始字节。创建示例仅示意字段，不应直接当作真实付费请求执行。

motion-guide.js 负责前端动作引导，仅将用户动作、镜头和节奏按本地规则组合为可编辑描述；app.js 负责显式写入后更新字数、时长建议和草稿。引导不发网络生成请求，也不识别图片。更换首帧清空面板临时选择，取消保留原描述，写入后的撤销仅作用于仍匹配的草稿。

```json
{
  "prompt": "镜头缓慢向前，保持雨夜街道的光线与构图",
  "aspect": "16:9",
  "duration": 8,
  "request_id": "example-request-unique-0001",
  "reference_preset": "city"
}
```

自传图将 reference_preset 替换为上传接口返回的 reference_id，两者互斥，aspect 必须与参考元数据一致；不接受任意文件路径、远程 URL 或其他会话的图片。两者都不带时为文生。相同 request_id 的提示词、时长、画幅和参考必须完全相同，任务模式随记录持久化；供应商模型由服务端决定，不接受前端自选。错误返回 error 和 code，前端依据状态解释，不能遇错就重新 POST。

## 持久化与故障恢复

SQLite 保存本地任务、会话归属摘要、输入参数、估价、状态、模型、参考及管理字段；reference_images 保存标准化图片的尺寸、归属和内容摘要，旧 jobs 表迁移新增可空 reference_id。刷新或重启继续处理原任务；若提交是否被上游接受不明，进入 UNCERTAIN 并人工核对，不自动重发。获得上游任务号后只查询该任务；下载失败仅重新取原结果。参考文件缺失时在调用供应商前明确拒绝。

视频下载仅允许指定阿里域名的 HTTPS 地址，不转发密钥、不跟随跳转，限制 100MiB 并检查 MP4 文件头。删除只加标记，不擦除文件。备份恢复时数据库、参考图和视频应成套保留；备份仍含私人记录，不进入代码仓库。移除创作区首帧不删除历史参考图，已有作品仍可查看和复用。

## 流畅播放与缓存

任务流程为提交、排队、模型生成、DOWNLOADING（取回原片）、PREPARING（准备在线播放文件）、SUCCEEDED（服务端文件可用）。SQLite 新增可空的 submitted_at、generation_completed_at、download_completed_at、ready_at，并通过归属校验后的 API 的 timings 返回。它们是本机观察时间；模型完成观察包含后台查询间隔，不能当作供应商精确计费或生成时长。旧作品不以 updated_at 补造历史时间。恢复 PREPARING 或已保存原片的 DOWNLOADING 时只加工本地文件，不再次调用模型。

浏览器新提交同时保存 created_at，以同一 request_id 关联到实际作品。计时从提交前持续到对应播放源达到 readyState >= 3 / canplay，保存在本机 localStorage 的 prelude.playable-timings.v1，最多 40 条，仅含请求标识和开始/可播放时间；退出或切换身份时清理。刷新保留首次计时，重新打开不会把旧成片年龄计为等待；缺少提交起点时不显示完整时长。该时长含本轮提交网络、服务端生成与准备、状态发现和首次媒体缓冲，不含随后 900ms 的视觉淡出，也不代表后续一定无缓冲。其他设备不能读取这份浏览器记录。

等待云海、计时和阶段显示持续覆盖播放准备；云海只在对应视频具备播放条件后淡出，不因 loadeddata 仅解出第一帧就揭晓。后续缓冲保留播放器，只更新状态文字，不重新载入或重新生成。活动任务通常每 5 秒同步，保存与准备阶段每 1.5 秒同步；断线时回到 10 秒同步。作品详情另显示服务端受理至文件准备完成的用时，明确排除浏览器缓冲。

`media.py` 在原片保存后生成私有 `.studio/playback/{id}.mp4` 和 `{id}.jpg`。H.264/yuv420p、CRF 23、最大目标码率 2200k、AAC 96k，保留尺寸与帧率，以 faststart 前置 MP4 索引；短片平均码率可能略高于设置值。封面最长边 480px。原片不变；流畅版比原片更大时不启用。文件锁、两条编码线程、90 秒上限与 0600 原子写入限制本地加工；失败回退原片，不重发或改变成功任务。

本机使用现有 FFmpeg 7.1 工具置于忽略的 `.tools/ffmpeg`；服务器需另装支持 H.264/AAC 解码及 libx264/AAC 编码的 FFmpeg，或设置 `PRELUDE_FFMPEG`。新任务缺少校验工具时在提交模型前拒绝；完整校验通过后，播放版加工失败仍可回退原片。备份包括 playback 目录，不把身份、媒体或备份提交仓库。

### 成片完整性与待核对恢复（2026-10-28）

下载的 Content-Length（若存在）必须与实际长度一致，最长 100MiB，原子替换前验证 MP4 顶层 atom 长度及 ftyp/moov/mdat。PREPARING 内完整解码视频和音轨，检查有效帧、H.264、时长误差不超过 0.6 秒；文生视频还校验请求尺寸。失败文件移入私有 quarantine，不暴露媒体 URL，也不设置 ready_at。加工失败不能触发模型重发。历史成功任务不自动改写；本轮另对现有 8 条原片作只读校验。

jobs 增加 recovery_attempts、next_check_at、last_checked_at。已知编号的 UNCERTAIN 支持原任务 GET 查询和原结果重新下载，连续恢复错误达到三次停止自动处理；用户可通过同源、CSRF 和所有权校验的 POST `/api/generations/{id}/check` 显式请求再查，保留冷却。未知编号不猜测结果、不释放额度、不重新 POST 生成。全局单活动任务保护继续存在，原状态确认后解除阻塞。

本机 `task_admin.py` 直接处理已有库，不构造 Studio，避免在线管理时误触启动恢复。`list` 只列待核对作品；`resolve` 仅处理 UNCERTAIN，要求记录核对依据。`attach --task-id-file` 绑定控制台已核实的原编号，拒绝覆盖或复用其他作品编号，随后由 worker 查询；`not-submitted` 仅允许无编号任务转 REJECTED；`failed` 转 FAILED；`completed-unavailable` 转 UNAVAILABLE 并保留费用与秒数占用。没有手动设为成功的入口。job_reviews 保存核对记录，拒收 URL/明显密钥。工作者、网页重查和管理员共用作品级 flock，状态更新与审计在同一数据库事务完成。管理员结论必须来自原任务证据；UNKNOWN/超时本身不构成失败证据。

时长建议支持中文数字、英文 second/sec/s、整体时长优先，排除“第2秒”、at/after 及时间范围；多项冲突时回到动作节奏建议，不按首个时间点截短。用户手动填写有效整数始终优先，推荐不自动覆盖输入。

API 保留 `video_url`，新增 `playback_url`、`poster_url`、`playback_optimized` 和两种大小。`/api/generations/{id}/preview`、`/poster` 和 `/video` 均先检查归属、成功和未删除，再响应 Range/HEAD、下载或 ETag。私有媒体 `private, no-cache`，304 也不绕过身份检查；公开 assets 一天缓存，响应不写入会话 Cookie。HTML、脚本、API 保持不缓存。流畅版下载名称带 `prelude-flow-`，界面分别提供两种下载。

前端只在媒体源改变时加载，采用流畅版与静态海报，列表使用惰性 JPG。云海可见且希望播放却 15 秒无时间进展时有限重试；成片播放、离屏、后台、主动暂停时停止背景。该逻辑不能恢复失效隧道或代替稳定托管。提示词中的 4K/8K 不改变服务端固定 720P 参数，动作过密的规则提示不改写用户输入或强制缩短时长。

`media.prepare_stream`以copy模式将现有播放版重新封装为fragmented MP4，保存到私有streams目录；不重新编码、不改动原画质或压缩版下载文件。缺少播放版时可从已验证原片封装。原子落盘前验证MP4结构与初始化配置；失败保留已有播放路径。API新增stream_url、stream_type、stream_size_bytes，私有`/api/generations/{id}/stream`沿用归属、状态、删除、Range/HEAD/ETag保护，MIME从自身生成文件的avcC读取。

`playback.js`在浏览器支持对应MediaSource类型时使用同一次fetch增量送入SourceBuffer；appendBuffer串行执行。仅开放当前时间所在连续缓冲区，起始时间允许0.15秒封装偏移；取全程与最近6秒吞吐量较小值的75%，结合剩余字节、片长和至少3秒画面判断启动（短片按自身时长）。等待界面还要求readyState>=3。收齐后保留Blob；播放中的MediaSource不被强制重载。第二播放器等待同一传输完成后复用Blob，防止把同一个MediaSource挂到两个视频上。

不支持MSE时完整载入；SourceBuffer或解码失败时完成同一次传输并回退Blob，不重复请求；尝试保留播放位置。超过32MiB的已知文件直接交给受鉴权的原生Range播放，不由应用完整读入Blob。分段路径没有绕开带宽限制，也不承诺公网零等待。实现依据[MediaSource](https://developer.mozilla.org/en-US/docs/Web/API/MediaSource)、[isTypeSupported](https://developer.mozilla.org/en-US/docs/Web/API/MediaSource/isTypeSupported_static)与[appendBuffer](https://developer.mozilla.org/en-US/docs/Web/API/SourceBuffer/appendBuffer)。

页面缓存最多三条、目标48MiB；活动条目不驱逐，原地重播与详情重开复用，不在跨页面持久保存。换片取消无使用者的未完成请求；25秒无字节或总计240秒中断并保留重开/下载指引。错误响应、非MP4、超限和不完整响应不缓存。释放旧的失败传输不能驱逐同键的新传输；退出身份、离页和删除时回收Blob及SourceBuffer引用。原画质下载优先，明确大小和压缩取舍。仅当缓存字节对应所选下载文件本身时才从Blob保存；分段文件不会替换原片或标准压缩MP4。原画质下载名带prelude-original-，压缩版保留prelude-flow-。

## 验证与部署前提

```sh
.venv/bin/python3 -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python3 -m unittest discover -s video-production -p 'test_*.py'
```

以上测试使用模拟供应商或本地 HTTP，不调用百炼、不产生生成费用。背景制作工具的 submit 才会真实提交，必须独立确认预算，不能纳入自动测试。

邀请身份、会话找回和个人秒数配额已在本地实现。对外内测仍需生产 Web 入口、HTTPS、持久磁盘与备份、后台任务进程管理、访问控制及运行监测。当前 ThreadingHTTPServer 继续只监听回环地址；Python 官方明确说明 [http.server 不推荐用于生产](https://docs.python.org/3/library/http.server.html)，不能通过直接改成 0.0.0.0 来宣称完成上线。生产适配必须保留同源、Host、Secure Cookie、上传限制及单后台工作者约束，并通过实际域名验证。

2026-10-27 登录用户指定的腾讯云后，总览显示云服务器与轻量应用服务器均为 0 台；云开发列表在上海有一个体验版环境，界面显示到期日为 2027-04-23。它是已有 CloudBase 环境，不能当作一台可直接运行当前 Python/SQLite/磁盘后台的服务器。腾讯云[云托管服务开发说明](https://cloud.tencent.com/document/product/1243/53551)要求无状态服务，不能依赖永久本地状态；若选这条部署路线，必须先适配任务、身份及私有媒体的外部持久化，并核对套餐和费用。尚未评估或修改该环境内已有应用。

同日进一步只读检查套餐页：当前套餐周期为 2026-10-23 至 2026-11-23，资源消耗显示 3800.18 点，已超过 3000 点免费额度；其中云托管 3577.51 点、PostgreSQL 222.66 点。按量付费开关关闭。云托管服务列表明确显示环境已隔离，两项已有服务均暂停；这些是既有环境用量，序幕尚未上传。资源点消耗不当作现金欠款，统计有延迟，后续应以控制台当时状态核对。

用户明确要求先评估复用现有腾讯云，涉及费用先告知。现有免费环境已不能直接承载新增内测；复用需要先核实付费恢复及已有服务的共享消耗，再将本地 SQLite、私有磁盘和任务工作者适配为云数据库、云存储及可靠任务机制。仅升级套餐不等于序幕已能上线。

上述腾讯云核对阶段未购买或续费资源、未修改防火墙或权限，也未上传密钥或私人作品。随后按用户选择开启本机临时分享，见下节；腾讯云持久托管仍未实施。静态网页上传到托管站点不等于部署这一套后端。

## 本机临时 HTTPS 分享

面向少量朋友试用，采用 [Cloudflare Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) 分配临时 `trycloudflare.com` 首页，无需购买服务器或固定域名。网页流量经 Cloudflare 转发，模型 Key 留在后端；私有作品仍按通行证归属校验。需保持电脑开机联网，临时地址不保证持续可用，正式运营仍考虑独立服务器。

`share_runtime.py` 复用生产 WSGI 和既有工作者；`deploy/gunicorn-share.conf.py` 仅监听两个回环 TCP 端口。4173 供本机 HTTP 使用原 Cookie，4181 仅接受本次精确 HTTPS 主机与可信连接元数据，使用 Secure 的 `__Host-prelude_session`。入口根据 Gunicorn 从实际监听套接字获得的 SERVER_PORT 区分，不能用 Host 或客户端自造的端口头切换为本机模式。关闭 PROXY protocol、下划线头映射和额外控制套接字；限速使用有效的 CF-Connecting-IP，忽略任意 X-Forwarded-For。无需改为 0.0.0.0，也不开放路由器或云防火墙端口。

`share_studio.py start` 在读取正式配置、启动连接前，要求 `.studio/share-validation.json` 存在最近一天的独立模拟公网验收记录，包括首页、登录上传、模拟成片播放及隔离，并确认零真实模型调用、正式数据未改动。随后检查活动任务、工具与端口，共用单进程锁。不能在原任务仍活跃时切换，也不能与备份并行启动：先结束任务、停止旧服务并等锁释放，再执行完整私有备份，最后启动分享。

当前已校验 `.tools/cloudflared` 为 Homebrew core 的 2026.10.4 arm64 bottle；下载来源与校验摘要记录在私有工具目录，不把二进制提交仓库。连接使用 QUIC、显式空配置、关闭自动更新和远程诊断，指标仅监听回环；不继承 TUNNEL_* 或 DASHSCOPE_* 环境变量。使用信息级日志，禁用调试请求日志。正式工作者单独接收模型凭据，连接工具与命令行参数不携带 Key。

`.studio/share-session.json` 记录当前首页和状态；私有日志为 0600。`share_studio.py status` 查询当前地址，`share_studio.py stop` 或双击“停止临时分享.command”写入当前运行的随机停止标记，关闭公网连接并保留本机后台。结束整个管理进程会停止连接和后台。macOS 仅在连接期间使用 `caffeinate -i` 防止空闲睡眠；合盖、关机和网络中断仍会断线。

重新启动可能分配新地址，Cookie 不跨域继承；同一数据目录中的账号、额度、参考图与视频保留，用户选“已有通行证”重新登录。私有邀请由邀请人逐人转交，不能发布到网页。生成依旧消耗百炼免费额度或余额，个人总秒数、每日标价预算和单活动任务保护同时生效。本轮未通过正式入口重新调用真实模型。

## 单机邀请内测部署准备

本阶段只准备 Linux 持久服务器上的邀请内测，尚未执行服务器安装。目录内新增 `production.py`、`requirements-production.txt`、`deploy/gunicorn.conf.py`、`deploy/Caddyfile`、`deploy/prelude.service`、两份 `.env.example` 和 Caddy 环境变量 drop-in `deploy/caddy-prelude.conf`。普通服务器承载网页及后台，模型计算仍由百炼承担。该方案保持 SQLite 和单后台工作者，不代表多机、多任务并行或公开商业版本已经实现。

### 请求入口与数据

`server.py` 的路由抽取为 `StudioRequest`，本地 `Handler` 和生产 `ProxyRequest` 共用身份、CSRF、上传校验、作品归属、预算及恢复逻辑。Gunicorn 仅监听 `127.0.0.1:4180`，1 个 gthread 工作进程、4 条请求线程；生成线程仍只有 1 个。启动持有数据目录的 `.server.lock`，同一数据目录不能启动第二个后台，锁在数据恢复前取得。禁止 `--preload`、增加 workers、热重载、自动滚动换 worker；部署更新使用停机备份后重启，避免重叠进程恢复同一任务。

生产入口要求配置精确的 `https://域名`，不接受路径、端口、通配符或 IP；只信任本机 Caddy 的连接、明确的 HTTPS 头及 Caddy 覆盖写入的 `X-Prelude-Client-IP`。忽略客户端的 `X-Forwarded-For`，登录限速使用经本机代理确认的来源地址。若以后接 CDN 或负载均衡，须另行配置并测试可信代理，不能直接把当前请求地址当成终端用户地址。

公网 Cookie 为 `__Host-prelude_session`，包含 Secure、HttpOnly、SameSite=Strict，不设置 Domain。网页及 API 必须从同一精确域名访问。只公开 `dist` 和既有校验后的 API，不公开配置、SQLite、参考图目录或视频目录；私有媒体仍通过登录账户校验后流式播放、下载，支持 Range 与 HEAD。

`GET /api/health` 仅返回 `{"ok":true}` 或 503，不创建会话，也不调用百炼；检查生成线程存活和数据库可读。该检查不能替代供应商可用性、余额或实际生成验收。

### 安装位置与配置

先核对服务器到期日、系统、内存、磁盘、带宽和现有站点，确认使用空闲的独立目录。计划代码放 `/opt/prelude`，持久数据放 `/var/lib/prelude`，私有配置放 `/etc/prelude/studio.env`。代码由管理员维护，服务以专用 `prelude` 非 root 用户运行；不要直接上传本机 `.venv`、`.env.local` 或整份含私有数据的项目目录。安装运行依赖使用 `python3 -m venv /opt/prelude/.venv` 与 `/opt/prelude/.venv/bin/python3 -m pip install -r /opt/prelude/requirements-production.txt`。

`deploy/studio.env.example` 的私有副本设为 root 所有、0600，填写真实 `PRELUDE_PUBLIC_ORIGIN`、`DASHSCOPE_API_KEY` 和北京业务空间主机名。默认保持 `PRELUDE_INVITE_ONLY=1`、`PRELUDE_DATA_DIR=/var/lib/prelude`、每日标价预算 3000 分。只有正式服务环境变量可提供生产密钥，缺失时启动失败，不回退读取开发者的本机配置。

使用 [Caddy 官方安装说明](https://caddyserver.com/docs/install) 安装服务器版 Caddy。将 `deploy/caddy.env.example` 的副本保存到 `/etc/caddy/prelude.env`，配置相同的 `PRELUDE_DOMAIN` 和证书通知邮箱；只把这份不含模型 Key 的环境文件加载到 Caddy。将 `deploy/caddy-prelude.conf` 放到 `/etc/systemd/system/caddy.service.d/prelude.conf`。`Caddyfile` 包含全局超时和站点块；如果服务器已有 Caddy 站点，应合并配置，不覆盖其他项目。

Caddy 自动签发 HTTPS 的前提是域名已正确解析、80/443 可达、证书存储可持久写入，参见[自动 HTTPS](https://caddyserver.com/docs/automatic-https)。Gunicorn 的 4180 端口保持本机监听。模型 Key 不进入浏览器、Caddy 配置或访问日志。默认不开启请求访问日志，不记录提示词、Cookie、带签名地址或请求正文。

安装后的命令示意如下，只在服务器和域名确认、配置已填写及公网影响获授权后执行；本轮没有执行这些操作。

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now prelude
sudo -u prelude /opt/prelude/.venv/bin/python3 /opt/prelude/access_admin.py --data-dir /var/lib/prelude invite --seconds 30 --days 7
sudo systemctl restart caddy
```

`prelude.service` 的工作目录是 `/opt/prelude`，安装到 `/etc/systemd/system/prelude.service` 后再运行上述命令。Caddy 重启前必须在加载相同环境变量的情况下运行 `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`。未填环境变量的模板不能启动。模板的 systemd 安全限制和具体 Linux 系统兼容性需上机验收。

### 备份、迁移与验收

`backup_studio.py` 仅在服务停止、进程锁空闲时备份完整数据，不会自行停止服务。备份数据库、账号、邀请码文件、参考图和视频，跳过运行锁和下载临时文件；拒绝符号链接及把备份写入数据目录内部，成品权限为 0600。维护窗口中也不要同时运行账号管理命令。命令示意：

```sh
sudo systemctl stop prelude
sudo -u prelude /opt/prelude/.venv/bin/python3 /opt/prelude/backup_studio.py --data-dir /var/lib/prelude --output-dir /var/lib/prelude-backups
sudo systemctl start prelude
```

备份输出目录需预先创建并授权给 `prelude`。备份包含私人作品与身份摘要，应另存一份受限的异机副本；本轮没有上传真实备份。恢复时先停服务，将归档中的 `prelude-data` 完整恢复到一个空目录，核对路径、所有者、权限和数据库后切换数据目录，再启动和登录验收，不覆盖正在运行的库。恢复旧备份可能落后于上游实际生成状态，需先核对待处理记录，不能以恢复为由重发不明请求。

上线若需带入本地四条作品与原通行证，必须成套迁移并在用户明确授权后传输；也可使用空白线上数据重新邀请。新的 HTTPS 域名不会继承 localhost Cookie，已有账号数据迁移后须重新登录。

本机验证范围为 WSGI 协议、真实 Gunicorn 临时进程、代理来源校验、HTTPS Cookie、身份与额度、上传及视频 Range、互斥启动和私有备份恢复；不调用真实供应商。最终上线还须通过实际域名完成新设备登录、独立账户隔离、参考图上传、生成等待与成片、刷新恢复、下载、服务重启及恢复演练。真实生成前先核对并确认模型费用。暂不添加公开注册、支付或新创作模块。

实现依据：[Gunicorn 配置](https://gunicorn.org/reference/settings/)、[Caddy 反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。生产依赖固定 Gunicorn 26.2.0，验证后再升级。
