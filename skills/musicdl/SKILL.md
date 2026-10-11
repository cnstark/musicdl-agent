---
name: musicdl
description: Use when downloading music or lossless FLAC via musicdl.
---

# musicdl 无损音乐下载

Python 库 `musicdl`(GitHub: CharlesPikachu/musicdl)是核心引擎,50+ 源。搜索/取 URL/下载全免费、无需登录。

## 环境

- 需要 Python **3.10+**(3.9 会因 `str | dict` 语法报错)
- 安装:`uv venv --python 3.13 .venv && uv pip install musicdl requests`

## 源选择(2026-10 实测)

- 回退顺序:**KugouMusicClient → KuwoMusicClient → NeteaseMusicClient**
  - 酷狗覆盖最好;酷我常给 24bit/48kHz Hi-Res;网易常给 24bit/192kHz 超大文件(150-240MB/首),放最后
  - 三者全部免登录、免 Cookie,直下真 FLAC(已用文件头验证)
- 咪咕 MiguMusicClient 只有 MP3;MyFreeMP3 只有损;多数第三方聚合站(放屁网/歌曲宝/下载吧等)已 403 被封;GDStudioMusicClient 当前 401 不可用
- 用 `MusicClient(music_sources=[单个源])` 逐源试,不要一次传多源(慢且难定位失败)

## 用法

```python
import logging; logging.disable(logging.CRITICAL)  # 关掉 musicdl 的进度条刷屏
from musicdl import musicdl
client = musicdl.MusicClient(music_sources=['KugouMusicClient'])
infos = [i for v in client.search('歌名 歌手').values() for i in v]
# SongInfo 关键字段: song_name, singers, ext('flac'), file_size, file_size_bytes, duration, download_url
```

批量下载歌单 CSV:用 `scripts/fetch_lossless.py`(逐首流水线:搜索选版→下载→校验文件头→回写CSV):

```bash
python scripts/fetch_lossless.py playlist.csv -o downloads --delay 1.5
# CSV 需带表头,至少包含 歌名,歌手 两列
```

## 关键规则

- **匹配必须歌名+歌手双校验**(去空白、全半角括号归一后互相包含),否则 Live 版/翻唱会拿错
- **合唱歌曲(歌手含 /)要求所有歌手都出现在结果中**,任一匹配会放进独唱版(如 MIKA/莫文蔚→MIKA 独唱);都不满足才回退
- **下载 URL 带签名、数小时过期**:现用现取,绝不缓存 URL
- 批量限速 1-2s/首;失败自动换源
- 取无损候选中**文件最小**的,避开网易 24/192 超大文件
- **版本陷阱**:同名 DJ 版/Live 版常比原版文件小,"取最小"会选错版本 → 先精确歌名匹配,再排除含 Live/DJ/伴奏/剪辑/cover 等标记的变体,最后才比文件大小(fetch_lossless.py 已内置此逻辑)
- **英文歌常带 "(Explicit)""(Clean)""(Remastered)" 后缀**,精确歌名匹配会把它们排除掉 → 精确匹配前先剥掉这类后缀,或退回包含匹配+专辑匹配
- **默认下载原版专辑(录音室)版本**;Live/演唱会/综艺版本只在明确要求时才下
- **搜不到时换写法重试**:兼容汉字(如 U+F90C 隆)和简繁/日文汉字差异会让搜索空手而归 → 关键词先做 `unicodedata.normalize('NFKC')`,仍无结果就只用歌名搜或换歌手名写法(矶村由纪子→磯村由紀子)

## 版本 QA 与可追溯

- **校验已下载文件版本是否正确**:对比实际文件大小与源端预期大小(酷狗 `raw_data.search.SQFileSize`,即 `SongInfo.file_size_bytes`),偏差 >2% 即为错版;本地用 `afinfo <file>`(macOS)或 `ffprobe` 核对时长。不要靠"听"来发现错版
- **记录源 ID 以便精准重下**:酷狗存 `raw_data.search.SQFileHash`(对应无损文件本身),比 `identifier`(普通版 hash)更精确
- **文件名比较前先做 `unicodedata.normalize('NFC', s)`**:含韩文/全角括号的文件名在不同系统间常因 NFC/NFD 不一致而匹配失败
