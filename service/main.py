"""
musicdl-agent — 无损音乐搜索下载服务
把我们验证过的流程封装成 HTTP API:
  搜索选版(酷狗→酷我→网易,精确歌名→排除变体→专辑匹配→合唱歌手全匹配→取最小)
  → 下载 → 校验文件头 → 存入音乐库目录
"""
import logging, re, time, unicodedata, uuid, json, os
from pathlib import Path
from threading import Lock

logging.disable(logging.CRITICAL)
import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from musicdl import musicdl

# API key 验证: 环境变量 MUSICDL_API_KEY 设置后, 除 /health 外所有请求需携带 key
# 请求头: X-API-Key: <key>  或  Authorization: Bearer <key>
API_KEY = os.environ.get('MUSICDL_API_KEY', '')

MUSIC_DIR = Path('/music')          # 容器内音乐库挂载点
RECORDS_FILE = MUSIC_DIR / '.musicdl-agent_records.json'  # 下载信息持久化(随音乐库目录)
LEGACY_RECORDS_FILE = MUSIC_DIR / '.musicdown_records.json'  # 旧版记录文件名,兼容读取
FALLBACK = ['KugouMusicClient', 'KuwoMusicClient', 'NeteaseMusicClient']
LOSSLESS = {'flac', 'ape', 'wav'}
MAGIC = {'flac': b'fLaC', 'ape': b'MAC ', 'wav': b'RIFF'}
VARIANT_MARKS = ('live', 'dj', '伴奏', '剪辑', 'cover', 'remix', '版', '现场')
SRC_SHORT = {'KugouMusicClient': 'kugou', 'KuwoMusicClient': 'kuwo', 'NeteaseMusicClient': 'netease'}

app = FastAPI(title='musicdl-agent')
tasks = {}
tasks_lock = Lock()


@app.middleware('http')
async def api_key_auth(request, call_next):
    if API_KEY and request.url.path not in ('/health', '/docs', '/openapi.json'):
        key = request.headers.get('x-api-key', '')
        if not key:
            auth = request.headers.get('authorization', '')
            key = auth[7:] if auth.lower().startswith('bearer ') else ''
        if key != API_KEY:
            return JSONResponse({'ok': False, 'error': 'unauthorized: invalid or missing api key'}, status_code=401)
    return await call_next(request)


def norm(s):
    return re.sub(r'\s+', '', (s or '').lower().replace('（', '(').replace('）', ')'))

def matched(info, title, artist):
    nt, na = norm(title), norm(artist)
    rn, ra = norm(info.song_name), norm(info.singers)
    if not (nt in rn or rn in nt):
        return False
    parts = [p for p in na.split('/') if p]
    return any(p in ra for p in parts) or na in ra or ra in na

def sanitize(s):
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()

def pick_version(cands, title, album=''):
    nt = norm(title)
    exact = [i for i in cands if norm(i.song_name) == nt]
    if exact:
        cands = exact
    elif nt and not any(m in nt for m in VARIANT_MARKS):
        clean = [i for i in cands if not any(m in norm(i.song_name) for m in VARIANT_MARKS)]
        if clean:
            cands = clean
    na = norm(album)
    if na:
        hits = [i for i in cands if na in norm(i.album) or norm(i.album) in na]
        if hits:
            cands = hits
    return sorted(cands, key=lambda i: i.file_size_bytes or 0)[0] if cands else None

def search_best(title, artist, album=''):
    """回退搜索: (SongInfo, source) / (None, None); errors 收集各源错误"""
    kw = f"{title} {artist.split('/')[0]}"
    errors = {}
    for src in FALLBACK:
        try:
            client = musicdl.MusicClient(music_sources=[src])
            client.search_size_per_source = 5
            infos = [i for v in client.search(kw).values() for i in v]
        except Exception as e:
            errors[src] = str(e)[:120]
            continue
        cands = [i for i in infos if i.ext in LOSSLESS and matched(i, title, artist)]
        # 合唱歌曲: 优先所有歌手都出现的候选, 防止独唱版混入
        parts = [p for p in norm(artist).split('/') if p]
        if len(parts) > 1 and cands:
            strong = [i for i in cands if all(p in norm(i.singers) for p in parts)]
            if strong:
                cands = strong
        best = pick_version(cands, title, album)
        if best and best.download_url:
            return best, src, errors
    return None, None, errors

def verify_file(fp, ext):
    magic = MAGIC.get(ext)
    if not magic:
        return True
    with open(fp, 'rb') as f:
        return f.read(4) == magic

records_lock = Lock()

def load_records():
    try:
        return json.loads(RECORDS_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    try:  # 兼容旧文件名
        return json.loads(LEGACY_RECORDS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}

def save_record(fname, rec):
    with records_lock:
        data = load_records()
        data[fname] = rec
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        RECORDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

def source_id(info, src):
    if src == 'KugouMusicClient':
        return info.raw_data.get('search', {}).get('SQFileHash') or info.identifier or ''
    return info.identifier or ''

def do_download(title, artist, album=''):
    """完整流水线, 返回结果 dict"""
    best, src, errors = search_best(title, artist, album)
    if not best:
        return {'ok': False, 'stage': 'search', 'errors': errors}
    fname = unicodedata.normalize('NFC', sanitize(f"{artist}-{title}.{best.ext}"))
    fp = MUSIC_DIR / fname
    try:
        r = requests.get(best.download_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=300, stream=True)
        r.raise_for_status()
        with open(fp, 'wb') as fo:
            for c in r.iter_content(1 << 20):
                fo.write(c)
    except Exception as e:
        fp.unlink(missing_ok=True)
        return {'ok': False, 'stage': 'download', 'error': str(e)[:200]}
    if not verify_file(fp, best.ext) or fp.stat().st_size < 1024 * 1024:
        fp.unlink(missing_ok=True)
        return {'ok': False, 'stage': 'verify', 'error': 'bad file header or too small'}
    result = {
        'ok': True, 'file': fname, 'path': str(fp), 'size': fp.stat().st_size,
        'source': SRC_SHORT.get(src, src), 'ext': best.ext,
        'source_id': source_id(best, src),
        'matched_name': best.song_name, 'matched_artist': best.singers,
        'matched_album': best.album, 'duration': best.duration,
    }
    save_record(fname, {
        'title': title, 'artist': artist, 'album': album,
        'source': result['source'], 'source_id': result['source_id'],
        'ext': best.ext, 'size': result['size'], 'duration': best.duration,
        'matched_name': best.song_name, 'matched_artist': best.singers,
        'matched_album': best.album, 'downloaded_at': int(time.time()),
    })
    return result


class Song(BaseModel):
    title: str
    artist: str
    album: str = ''

class BatchReq(BaseModel):
    songs: list[Song]

@app.get('/health')
def health():
    return {'ok': True, 'music_dir': str(MUSIC_DIR)}

@app.get('/search')
def search(q: str = '', title: str = '', artist: str = '', album: str = ''):
    """返回候选(含 source + id, 供 /download_select 选用)"""
    if not q and title:
        q = f"{title} {artist.split('/')[0]}"
    out = []
    for src in FALLBACK:
        try:
            client = musicdl.MusicClient(music_sources=[src])
            client.search_size_per_source = 5
            infos = [i for v in client.search(q).values() for i in v]
            for i in infos:
                if i.ext in LOSSLESS:
                    out.append({'source': SRC_SHORT.get(src, src), 'id': str(i.identifier),
                                'name': i.song_name, 'artist': i.singers, 'album': i.album,
                                'duration': i.duration, 'ext': i.ext, 'size': i.file_size})
        except Exception:
            continue
    return {'results': out}

SRC_FULL = {v: k for k, v in SRC_SHORT.items()}

class SelectReq(BaseModel):
    title: str
    artist: str
    source: str           # kugou / kuwo / netease
    id: str               # /search 返回的 id
    album: str = ''

@app.post('/download_select')
def download_select(req: SelectReq):
    """人工/LLM 选定版本后按 id 下载: 重搜拿新鲜 URL → 校验 → 入库 → 记录"""
    src_full = SRC_FULL.get(req.source)
    if not src_full:
        return {'ok': False, 'error': f'unknown source: {req.source}'}
    kw = f"{req.title} {req.artist.split('/')[0]}"
    try:
        client = musicdl.MusicClient(music_sources=[src_full])
        client.search_size_per_source = 10
        infos = [i for v in client.search(kw).values() for i in v]
    except Exception as e:
        return {'ok': False, 'stage': 'search', 'error': str(e)[:200]}
    best = next((i for i in infos if str(i.identifier) == req.id), None)
    if not best or not best.download_url:
        return {'ok': False, 'stage': 'search', 'error': 'id 未命中, 可能搜索结果已变化, 请重新 /search'}
    fname = unicodedata.normalize('NFC', sanitize(f"{req.artist}-{req.title}.{best.ext}"))
    fp = MUSIC_DIR / fname
    try:
        r = requests.get(best.download_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=300, stream=True)
        r.raise_for_status()
        with open(fp, 'wb') as fo:
            for c in r.iter_content(1 << 20):
                fo.write(c)
    except Exception as e:
        fp.unlink(missing_ok=True)
        return {'ok': False, 'stage': 'download', 'error': str(e)[:200]}
    if not verify_file(fp, best.ext) or fp.stat().st_size < 1024 * 1024:
        fp.unlink(missing_ok=True)
        return {'ok': False, 'stage': 'verify', 'error': 'bad file header or too small'}
    result = {
        'ok': True, 'file': fname, 'path': str(fp), 'size': fp.stat().st_size,
        'source': req.source, 'ext': best.ext, 'source_id': source_id(best, src_full),
        'matched_name': best.song_name, 'matched_artist': best.singers,
        'matched_album': best.album, 'duration': best.duration,
    }
    save_record(fname, {
        'title': req.title, 'artist': req.artist, 'album': req.album,
        'source': req.source, 'source_id': result['source_id'],
        'ext': best.ext, 'size': result['size'], 'duration': best.duration,
        'matched_name': best.song_name, 'matched_artist': best.singers,
        'matched_album': best.album, 'downloaded_at': int(time.time()),
    })
    return result

@app.post('/download')
def download(s: Song):
    return do_download(s.title, s.artist, s.album)

def run_batch(tid, songs):
    with tasks_lock:
        tasks[tid] = {'done': 0, 'total': len(songs), 'results': [], 'running': True}
    for s in songs:
        res = do_download(s.title, s.artist, s.album)
        res['title'], res['artist'] = s.title, s.artist
        with tasks_lock:
            tasks[tid]['results'].append(res)
            tasks[tid]['done'] += 1
        time.sleep(1.5)
    with tasks_lock:
        tasks[tid]['running'] = False

@app.post('/batch')
def batch(req: BatchReq):
    import threading
    tid = uuid.uuid4().hex[:8]
    threading.Thread(target=run_batch, args=(tid, req.songs), daemon=True).start()
    return {'task_id': tid, 'total': len(req.songs)}

@app.get('/tasks/{tid}')
def task_status(tid: str):
    with tasks_lock:
        t = tasks.get(tid)
    if not t:
        return {'error': 'not found'}
    return t

@app.get('/songs')
def list_songs():
    """音乐库列表: 文件 + 下载信息(来源/ID/时长等)"""
    recs = load_records()
    songs = []
    for fp in sorted(MUSIC_DIR.iterdir()):
        if fp.suffix.lower().lstrip('.') not in LOSSLESS | {'mp3', 'm4a'}:
            continue
        item = {'file': fp.name, 'size': fp.stat().st_size,
                'size_mb': round(fp.stat().st_size / 1048576, 1)}
        item.update(recs.get(fp.name, {}))
        item['has_info'] = fp.name in recs
        songs.append(item)
    return {'total': len(songs), 'with_info': sum(1 for s in songs if s['has_info']), 'songs': songs}

class RecordItem(BaseModel):
    file: str
    title: str = ''
    artist: str = ''
    album: str = ''
    source: str = ''
    source_id: str = ''
    ext: str = ''
    duration: str = ''
    downloaded_at: int = 0

class RecordsImport(BaseModel):
    records: list[RecordItem]

@app.post('/records/import')
def import_records(req: RecordsImport):
    """批量导入历史下载信息(如从歌单 CSV)"""
    with records_lock:
        data = load_records()
        n = 0
        for r in req.records:
            if not r.file:
                continue
            rec = r.model_dump()
            rec.pop('file')
            data[r.file] = rec
            n += 1
        RECORDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'imported': n, 'total_records': len(data)}
