# musicdl-agent — 个人无损音乐下载服务

把「搜索 → 智能选版 → 下载 → 校验 → 入库」整套流程封装成跑在 NAS 上的 HTTP API 服务,AI agent(Hermes / OpenClaw 等)可直接调用。

基于 [musicdl](https://github.com/CharlesPikachu/musicdl)(50+ 音乐源),默认使用**免费、免登录**的无损源:酷狗 / 酷我 / 网易云,自动回退。

> 仅供个人学习使用,请支持正版音乐。

## 架构

```
┌─────────────┐   HTTP    ┌──────────────────────────────┐
│  AI Agent   │ ────────► │  musicdl-agent (NAS Docker)  │
│ (带 skill)  │           │                              │
└─────────────┘           │  搜索选版 → 下载 → 校验 → 入库 │
                          └──────────────┬───────────────┘
                                         ▼
                                  NAS 音乐目录 (FLAC)
                                  + .musicdl-agent_records.json
                                  (每首的来源/ID/时长记录)
```

### 智能选版规则(踩坑沉淀)

1. 歌名 + 歌手双匹配(去空白、全半角归一)
2. 合唱歌曲要求**所有歌手**都出现(防止 MIKA/莫文蔚 匹配到 MIKA 独唱)
3. 精确歌名优先,排除 Live/DJ/伴奏/剪辑/cover 等变体
4. 有专辑名时优先匹配原版专辑
5. 无损候选中取文件最小的(避开 24bit/192kHz 超大文件)
6. 下载后校验文件头(fLaC magic)+ 大小下限
7. 记录源平台 ID(如酷狗 SQFileHash),可精准重下同一版本

## 快速开始

### 方式零:直接用预构建镜像(推荐)

每次发 tag 自动构建多架构镜像(amd64/arm64)并推送到 GHCR:

```bash
docker pull ghcr.io/cnstark/musicdl-agent:latest
docker run -d --name musicdl-agent \
  -p 8700:8700 \
  -v /path/to/your/music:/music \
  --restart unless-stopped \
  ghcr.io/cnstark/musicdl-agent:latest
```

### 方式一:Docker Compose

```yaml
# docker-compose.yml
services:
  musicdl-agent:
    image: ghcr.io/cnstark/musicdl-agent:latest   # 或 build: ./service 自行构建
    ports:
      - "8700:8700"
    volumes:
      - /path/to/your/music:/music   # 改成你的音乐目录
    restart: unless-stopped
```

```bash
docker compose up -d --build
```

### 方式二:手动构建

```bash
cd service
docker build -t musicdl-agent .
docker run -d --name musicdl-agent \
  -p 8700:8700 \
  -v /path/to/your/music:/music \
  --restart unless-stopped \
  musicdl-agent
```

首次启动需要 10-15 秒加载(musicdl 初始化),期间请求可能 502,稍后重试即可。

## API

Base URL: `http://<your-host>:8700`

| 接口 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `GET /search?title=X&artist=Y` | 三源无损候选,含 `source` + `id` |
| `POST /download` | 自动选版(原版专辑)下载。`{"title","artist","album"}` |
| `POST /download_select` | 按 id 下载指定版本。`{"title","artist","source","id","album"}` |
| `POST /batch` | 批量下载。`{"songs":[{"title","artist","album"}...]}` → task_id |
| `GET /tasks/{id}` | 批量任务进度 |
| `GET /songs` | 曲库列表 + 下载信息 |
| `POST /records/import` | 批量导入历史下载记录 |

### 示例

自动下载(默认原版专辑):

```bash
curl -X POST http://<host>:8700/download \
  -H "Content-Type: application/json" \
  -d '{"title":"演员","artist":"薛之谦","album":"绅士"}'
```

人工/LLM 选版(Live、Remix 等特殊版本):

```bash
# 1. 搜索拿候选
curl "http://<host>:8700/search?title=消愁&artist=毛不易"

# 2. 选定后用 id 下载(服务会重新解析新鲜下载地址,URL 不过期问题不存在)
curl -X POST http://<host>:8700/download_select \
  -H "Content-Type: application/json" \
  -d '{"title":"消愁","artist":"毛不易","source":"kugou","id":"<id-from-search>"}'
```

## Agent Skills

`skills/` 目录提供开箱即用的 agent 技能,兼容 Hermes / OpenClaw 等支持 SKILL.md 的框架:

- **`musicdl-agent/`** — 教 agent 调用本服务(接口、两条工作流、注意事项)。复制到 agent 的 skills 目录即可。使用前把文档里的 `<your-host>` 换成你的 NAS 地址。
- **`musicdl/`** — musicdl 使用经验沉淀(可用源、匹配规则、坑),附批量下载脚本 `scripts/fetch_lossless.py`(逐首流水线:选版→下载→校验→回写 CSV→清理)。

### fetch_lossless.py

```bash
pip install musicdl requests
python skills/musicdl/scripts/fetch_lossless.py playlist.csv -o downloads --delay 1.5
# CSV 需带表头,至少包含 歌名,歌手 两列
```

## 已知限制

- 咪咕只有 MP3;大部分第三方聚合站已被封,默认不用
- 部分冷门曲目(如 B 站 UP 主翻唱)三个免费源都没有,需要人工渠道
- 下载地址带签名数小时过期,服务始终现用现取,不缓存 URL
