#!/usr/bin/env python3
"""
fetch_lossless.py — 按 CSV 歌单逐首下载无损音乐(免费源,无需登录)

用法:
  python fetch_lossless.py playlist.csv [-o 输出目录] [--skip N] [--limit N] [--delay 1.5]

CSV 格式: 带表头,至少包含 歌名,歌手 两列(如有 专辑 列会用于选版)

每首歌依次完成(不是批量阶段式):
  搜索选版(酷狗→酷我→网易回退,精确歌名→排除Live/DJ变体→专辑匹配→取最小)
  → 下载 → 校验 FLAC/APE/WAV 文件头 → 回写 CSV(已下载✓/来源/酷狗SQ_ID)

注意: 下载 URL 带签名、数小时后过期,现用现取,不要缓存
"""
import argparse, csv, logging, re, time, unicodedata
from pathlib import Path

logging.disable(logging.CRITICAL)
import requests
from musicdl import musicdl

FALLBACK = ['KugouMusicClient', 'KuwoMusicClient', 'NeteaseMusicClient']
LOSSLESS = {'flac', 'ape', 'wav'}
MAGIC = {'flac': b'fLaC', 'ape': b'MAC ', 'wav': b'RIFF'}
VARIANT_MARKS = ('live', 'dj', '伴奏', '剪辑', 'cover', 'remix', '版', '现场')
SRC_SHORT = {'KugouMusicClient': '酷狗', 'KuwoMusicClient': '酷我', 'NeteaseMusicClient': '网易'}


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
    """版本选择: 精确歌名 → 排除Live/DJ等变体 → 专辑匹配 → 取文件最小(避开网易24/192超大文件)"""
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
    """回退搜索, 返回 (SongInfo, 源名) 或 (None, None)"""
    kw = f"{title} {artist.split('/')[0]}"
    for src in FALLBACK:
        try:
            client = musicdl.MusicClient(music_sources=[src])
            client.search_size_per_source = 5
            infos = [i for v in client.search(kw).values() for i in v]
        except Exception:
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
            return best, src
    return None, None

def verify_file(fp, ext):
    magic = MAGIC.get(ext)
    if not magic:
        return True
    with open(fp, 'rb') as f:
        return f.read(4) == magic

def source_id(info, src):
    if src == 'KugouMusicClient':
        return info.raw_data.get('search', {}).get('SQFileHash') or info.identifier or ''
    return info.identifier or ''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('csv')
    ap.add_argument('-o', '--outdir', default='./downloads')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--skip', type=int, default=0, help='跳过前 N 行')
    ap.add_argument('--delay', type=float, default=1.5)
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    with open(args.csv, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    header = rows[0]
    for col in ('已下载', '来源', '酷狗SQ_ID'):
        if col not in header:
            header.append(col)
    i_no, i_dl, i_src, i_sq = header.index('序号'), header.index('已下载'), header.index('来源'), header.index('酷狗SQ_ID')
    i_title, i_artist = header.index('歌名'), header.index('歌手')
    i_album = header.index('专辑') if '专辑' in header else None

    todo = rows[1:]
    if args.skip:
        todo = todo[args.skip:]
    if args.limit:
        todo = todo[:args.limit]

    stats = {'ok': 0, 'fail': 0}
    failed = []
    for r in todo:
        r += [''] * (len(header) - len(r))
        title, artist = r[i_title], r[i_artist]
        album = r[i_album] if i_album is not None else ''
        tag = f"[{r[i_no]}] {artist}-{title}"
        try:
            best, src = search_best(title, artist, album)
            if not best:
                print(f"{tag} ✗ 未找到无损", flush=True)
                stats['fail'] += 1; failed.append(r); continue
            fname = unicodedata.normalize('NFC', sanitize(f"{artist}-{title}.{best.ext}"))
            fp = outdir / fname
            if fp.exists() and fp.stat().st_size > 1024 * 1024:
                print(f"{tag} - 已存在跳过", flush=True)
                stats['ok'] += 1; continue
            # 下载
            resp = requests.get(best.download_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=120, stream=True)
            resp.raise_for_status()
            with open(fp, 'wb') as fo:
                for c in resp.iter_content(1 << 20):
                    fo.write(c)
            # 校验文件头
            if not verify_file(fp, best.ext) or fp.stat().st_size < 1024 * 1024:
                print(f"{tag} ✗ 校验失败", flush=True)
                fp.unlink(missing_ok=True)
                stats['fail'] += 1; failed.append(r); continue
            # 回写 CSV
            r[i_dl] = '✓'
            r[i_src] = f"{SRC_SHORT.get(src, src)}FLAC"
            if src == 'KugouMusicClient':
                r[i_sq] = source_id(best, src)
            with open(args.csv, 'w', newline='', encoding='utf-8-sig') as f:
                csv.writer(f).writerows([header] + rows[1:])
            stats['ok'] += 1
            print(f"{tag} ✓ {SRC_SHORT.get(src, src)} {best.ext} {best.file_size or ''}", flush=True)
            time.sleep(args.delay)
        except Exception as e:
            print(f"{tag} ✗ 错误: {str(e)[:120]}", flush=True)
            stats['fail'] += 1; failed.append(r)

    if failed:
        with open(outdir / '_failed.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f); w.writerow(header); w.writerows(failed)
    print(f"\n完成: 成功 {stats['ok']} 失败 {stats['fail']}", flush=True)

if __name__ == '__main__':
    main()
