---
name: musicdl-agent
description: Use when downloading music via the musicdl-agent service.
---

# musicdl-agent — 无损音乐下载服务

跑在服务器/NAS 上的 FastAPI 服务,封装 musicdl 搜索/下载/校验/入库全流程。下载直接写入服务端音乐目录。

## 基本信息

- 地址:`http://<your-host>:8700`(部署时替换;下同)
- 音乐库:服务端配置的 `/music` 挂载目录
- 免费源:kugou / kuwo / netease,无需登录,默认无损 FLAC
- **服务启动慢(musicdl 加载),重启后首次请求可能 502,等 10-15s 重试**

## 接口

| 接口 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `GET /search?title=X&artist=Y` | 三源无损候选,每项含 `source`+`id`、歌名、歌手、专辑、时长、大小 |
| `POST /download` | **自动选版**(原版专辑)下载入库。body: `{"title","artist","album"}` |
| `POST /download_select` | **按 id 精准下载**指定版本。body: `{"title","artist","source","id","album"}` |
| `POST /batch` | 批量自动下载。body: `{"songs":[{"title","artist","album"}...]}`,返回 task_id |
| `GET /tasks/{id}` | 批量任务进度/结果 |
| `GET /songs` | 曲库列表+下载信息(来源、source_id、时长、大小) |
| `POST /records/import` | 批量补录历史下载信息 |

## 标准工作流

**常规歌曲(要原版专辑)** → 直接 `POST /download`:

```bash
curl -X POST http://<your-host>:8700/download -H "Content-Type: application/json" \
  -d '{"title":"演员","artist":"薛之谦","album":"绅士"}'
```

**特殊版本(Live/Remix/翻唱/DJ,或同名歌曲多)** → 两步,先搜索再按 id 下载:

1. `GET /search?title=X&artist=Y` 拿候选列表
2. 根据专辑名/时长/版本后缀选定,取该条的 `source` 和 `id`
3. `POST /download_select` 传入,服务端重新解析下载地址后下载

```bash
curl -X POST http://<your-host>:8700/download_select -H "Content-Type: application/json" \
  -d '{"title":"了表心意","artist":"薛之谦/伊一","source":"kugou","id":"<id-from-search>","album":"无限歌谣季 第14期"}'
```

## 关键规则

- **id 是稳定引用,下载 URL 带签名数小时过期**:download_select 总是重新搜索解析,绝不让调用方传 URL
- id 未命中返回 `id 未命中` 错误 → 重新 `/search` 再选
- 文件名规范:`歌手-歌名.flac`(`/` 转 `_`,NFC 归一化),保存在音乐库根目录
- 下载信息自动记录到音乐库 `.musicdl-agent_records.json`(含 source_id),`/songs` 可查
- 默认原版专辑(录音室)版本;Live/演唱会版只在明确要求时通过 download_select 下
