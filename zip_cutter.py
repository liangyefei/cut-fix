"""Extract source and construct ZIP project chunks and code graphs."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import uuid
import zipfile
from collections import Counter
from contextlib import closing
from dataclasses import asdict
from functools import lru_cache
from itertools import accumulate
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from .aps import split_aps
from .jcl import split_jcl
from .quality import prepare_text


MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_SOURCE = 1024 * 1024
MAX_FILES = 5000
SOURCE_SPLITTERS = {'.aps': split_aps, '.jcl': split_jcl}
EXTENSIONS = {'.cbl', '.cob', '.cobol', '.cpy'} | SOURCE_SPLITTERS.keys()
ENGINE_NAMES = {'codegraph': 'CodeGraph', '.aps': 'APS', '.jcl': 'JCL'}
IGNORED = {'.git', '.codegraph', 'node_modules', '__macosx'}


def inspect_archive(content: bytes, encoding: str = 'utf-8') -> tuple[dict[str, str], list[dict]]:
    """Validate BEFORE writing. Never extract archive-provided config or executables."""
    if encoding not in {'utf-8', 'gb18030'}:
        raise ValueError('源码编码只支持 UTF-8 或 GB18030')
    if len(content) > MAX_UPLOAD:
        raise ValueError('ZIP 不能超过 50 MB')
    sources, skipped, seen = {}, [], set()
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES:
                raise ValueError('ZIP 条目不能超过 5000 个')
            if sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ValueError('ZIP 解压总量不能超过 200 MB')
            for entry in entries:
                name = entry.filename.replace('\\', '/')
                p = PurePosixPath(name)
                if (p.is_absolute() or '..' in p.parts or not p.parts
                        or any(':' in s or s.endswith((' ', '.')) or
                               re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', s)
                               for s in p.parts)
                        or any(ord(c) < 32 for c in name) or len(name) > 240):
                    raise ValueError(f'ZIP 含不安全路径：{name[:100]}')
                if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise ValueError('不支持符号链接或加密 ZIP')
                normalized = p.as_posix()
                if normalized.casefold() in seen:
                    raise ValueError(f'ZIP 存在重复或大小写冲突路径：{normalized}')
                seen.add(normalized.casefold())
                if entry.is_dir():
                    continue
                if len(p.parts) > 20:
                    raise ValueError('目录深度不能超过 20 层')
                if any(s.lower() in IGNORED for s in p.parts) or p.suffix.lower() not in EXTENSIONS:
                    skipped.append({'path': normalized, 'reason': '仅解析 COBOL、Copybook、APS 和 JCL，其他文件保留在原始 ZIP 中'})
                    continue
                if entry.file_size > MAX_SOURCE:
                    raise ValueError(f'{normalized} 超过单个源码文件 1 MB 限制')
                raw = archive.read(entry)
                try:
                    source = raw.decode('utf-8-sig' if encoding == 'utf-8' else encoding)
                except UnicodeDecodeError:
                    skipped.append(dict(path=normalized, reason='不是所选编码，请检查源码编码', quarantined=True))
                    continue
                if '\x00' in source:
                    skipped.append(dict(path=normalized, reason='包含二进制内容', quarantined=True))
                    continue
                if len(source.encode('utf-8')) > MAX_SOURCE:
                    raise ValueError(f'{normalized} 转为 UTF-8 后超过 1 MB')
                try:
                    source, _ = prepare_text(source, code=True)
                except ValueError as exc:
                    skipped.append(dict(path=normalized, reason=str(exc), quarantined=True))
                    continue
                sources[normalized] = source
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ValueError('ZIP 损坏或使用了不支持的压缩方式') from exc
    if not sources:
        reasons = '；'.join(f"{item['path']}: {item['reason']}" for item in skipped[:3])
        raise ValueError('ZIP 中未找到可用的 .cbl / .cob / .cobol / .cpy / .aps / .jcl 文件' + ('；' + reasons if reasons else ''))
    return sources, skipped


def run_codegraph(root: Path) -> dict:
    bridge = Path(__file__).resolve().parent / 'tools' / 'codegraph' / 'index-project.cjs'
    node = os.environ.get('KB_CODEGRAPH_NODE') or shutil.which('node')
    bundled_node = bridge.parent / 'node_modules/@colbymchenry/codegraph-win32-x64/node.exe'
    if not os.environ.get('KB_CODEGRAPH_NODE') and os.name == 'nt' and bundled_node.exists():
        node = str(bundled_node)
    if not node:
        raise ValueError('未找到 Node.js，请安装 Node 22.5+ 并设置 KB_CODEGRAPH_NODE')
    log = root / 'codegraph.log'
    with log.open('w', encoding='utf-8') as output:
        try:
            result = subprocess.run([node, '--liftoff-only', str(bridge), str(root / 'source')],
                                    cwd=bridge.parent, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=600, shell=False,
                                    env={**os.environ, 'CODEGRAPH_TELEMETRY': '0', 'DO_NOT_TRACK': '1'})
        except subprocess.TimeoutExpired as exc:
            raise ValueError('CodeGraph 解析超过 10 分钟，请缩小工程后重试') from exc
    if result.returncode:
        tail = log.read_text('utf-8', errors='replace')[-1200:]
        raise ValueError(f'CodeGraph 执行失败，请检查依赖安装。{tail}')
    database = root / 'source' / '.codegraph' / 'codegraph.db'
    if not database.exists():
        raise ValueError('CodeGraph 未生成索引数据库')
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
                for table in ('nodes', 'edges', 'files', 'unresolved_refs')}


def source_engine(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return suffix if suffix in SOURCE_SPLITTERS else 'codegraph'


def append_source_graph(raw: dict, source_root: Path, paths: list[str]) -> None:
    for path in paths:
        suffix = source_engine(path)
        engine = suffix[1:]
        source = (source_root / path).read_text('utf-8')
        blocks = SOURCE_SPLITTERS[suffix](source)
        file_id = f'{engine}:{path}:0'
        raw['files'].append(dict(path=path, engine=engine,
                                 errors='' if blocks else f'未识别到 {ENGINE_NAMES[suffix]} 结构关键字，请检查源码的固定列位置'))
        raw['nodes'].append(dict(id=file_id, file_path=path, name=Path(path).name, kind='file',
                                 start_line=1, end_line=len(source.splitlines())))
        for block in blocks:
            statement = block.statement_line or block.start_line
            node_id = f'{engine}:{path}:{statement}'
            node = dict(id=node_id, file_path=path, name=block.title, kind=block.kind,
                        start_line=block.start_line, end_line=block.end_line, source_keyword=block.keyword)
            if block.statement_line is not None:
                node['statement_line'] = statement
            if block.context:
                node['source_context'] = [asdict(ref) for ref in block.context]
            parent_id = f'{engine}:{path}:{block.parent_line}' if block.parent_line else file_id
            raw['nodes'].append(node)
            raw['edges'].append(dict(source=parent_id, target=node_id, kind='contains', line=statement))


def build_source_graph(root: Path, paths: list[str]) -> dict:
    """Parse the validated source files under root/source using their configured engines."""
    native_paths = [path for path in paths if source_engine(path) in SOURCE_SPLITTERS]
    raw = (run_codegraph(root) if len(native_paths) < len(paths)
           else dict(nodes=[], edges=[], files=[], unresolved_refs=[]))
    append_source_graph(raw, root / 'source', native_paths)
    return raw


def adapt_graph(raw: dict, root: Path, options: dict, job_id: str) -> dict:
    files = set(options['files'])
    prefix = f'project/{job_id}/'
    nodes, chunks = [], []
    has_structure = False
    by_original = {}
    common = dict(project=options['project'], version=options['version'],
                  project_job=job_id, scope=options['scope'],
                  library=options.get('library', ''), tags=options.get('tags', []), purpose='source')

    def relative(value: str) -> str:
        path = Path(value)
        return (path.relative_to(root / 'source').as_posix() if path.is_absolute()
                else value.replace('\\', '/'))

    @lru_cache(maxsize=1)
    def source_ranges(path: str) -> tuple[str, list[int]]:
        text = (root / 'source' / path).read_text('utf-8')
        return text, list(accumulate(map(len, text.splitlines(keepends=True)), initial=0))

    indexed = {relative(f['path']): f for f in raw['files']}
    labels = {'module': 'CodeUnit', 'function': 'CodeUnit', 'file': 'CodeFile',
              'variable': 'Field', 'field': 'Field', 'constant': 'Field', 'import': 'CopyBook'}
    for n in raw['nodes']:
        path = relative(n['file_path'])
        if path not in files:
            continue
        text, offsets = source_ranges(path)
        start, end = max(1, n['start_line']), min(len(offsets) - 1, n['end_line'])
        if start > end:
            continue
        nid = prefix + n['id']
        props = {**common, 'name': n['name'], 'kind': n['kind'], 'path': path,
                 'engine': indexed.get(path, {}).get('engine', 'codegraph'),
                 'source_path': f'kb-raw/projects/{job_id}/source/{path}',
                 'start_line': start, 'end_line': end, 'loc': f'L{start}-L{end}'}
        has_source_block = 'source_keyword' in n
        if has_source_block:
            props[f'{props["engine"]}_keyword'] = n['source_keyword']
        if 'statement_line' in n:
            props['statement_line'] = n['statement_line']
        node = dict(id=nid, label=labels.get(n['kind'], 'CodeUnit'), props=props)
        nodes.append(node)
        by_original[n['id']] = node
        if n['kind'] in {'module', 'function', 'variable', 'field'} or has_source_block:
            has_structure = True
        # Keep each recognized structural unit whole, including long sections/paragraphs.
        if n['kind'] in {'function', 'variable', 'constant'} or has_source_block:
            first, last = offsets[start - 1], offsets[end]
            chunk = dict(id=hashlib.sha256(f'{nid}:{start}'.encode()).hexdigest()[:32],
                         node_id=nid, path=path, title=n['name'], start_line=start, end_line=end,
                         text=text[first:last], start_offset=first, end_offset=last)
            if 'statement_line' in n:
                chunk['statement_line'] = n['statement_line']
            if 'source_context' in n:
                chunk[f'{props["engine"]}_context'] = n['source_context']
            chunks.append(chunk)
    # Files with no recognized paragraph/data item remain readable and searchable;
    # fallback is explicit and must not claim a structural parse succeeded.
    covered = {ch['path'] for ch in chunks}
    for path in sorted(files - covered):
        text, offsets = source_ranges(path)
        for first in range(1, len(offsets), 100):
            last = min(first + 99, len(offsets) - 1)
            chunks.append(dict(id=hashlib.sha256(f'{prefix}{path}:{first}'.encode()).hexdigest()[:32],
                               node_id='', path=path, title=f'{Path(path).name}（文件切片）',
                               start_line=first, end_line=last, text=text[offsets[first - 1]:offsets[last]],
                               start_offset=offsets[first - 1], end_offset=offsets[last]))
    edges = []
    for e in raw['edges']:
        if e['source'] in by_original and e['target'] in by_original:
            edges.append(dict(src=prefix + e['source'], dst=prefix + e['target'],
                              rel=e['kind'].upper(), props={**common, 'engine': by_original[e['source']]['props']['engine'],
                                                          'line': e.get('line') or 0,
                                                          'metadata': e.get('metadata') or ''}))
    unresolved = []
    for ref in raw['unresolved_refs']:
        origin = by_original.get(ref['from_node_id'])
        unresolved.append(dict(name=ref['reference_name'], kind=ref['reference_kind'],
                               path=origin['props']['path'] if origin else ref.get('file_path', ''),
                               line=ref['line']))
    chunk_counts = Counter(ch['path'] for ch in chunks)
    report_files = [dict(path=p, indexed=p in indexed,
                         errors=indexed.get(p, {}).get('errors') or '',
                         structural_chunks=p in covered,
                         chunks=chunk_counts[p]) for p in sorted(files)]
    engines = {source_engine(path) for path in files}
    if not has_structure:
        if engines != {'codegraph'}:
            raise ValueError(f'工程中未识别到可切分的 {options["engine"]} 结构；请检查源码格式、固定列位置和编码')
        raise ValueError('CodeGraph 未提取到 COBOL 结构；请检查版本、格式和源码编码')
    return dict(nodes=nodes, edges=edges, chunks=chunks, files=report_files, unresolved=unresolved,
                summary=dict(files=len(files), nodes=len(nodes), edges=len(edges), chunks=len(chunks),
                             unresolved=len(unresolved), skipped=len(options['skipped']),
                             warnings=sum(not f['indexed'] or bool(f['errors'] and f['errors'] != '[]')
                                          or not f['structural_chunks'] for f in report_files)),
                engine_version='1.6.2' if 'codegraph' in engines else '')


def cut_zip(content: bytes, *, project: str, version: str = 'v1.0',
            library: str = 'code-projects', encoding: str = 'utf-8',
            tags: list[str] | None = None, job_id: str | None = None) -> dict:
    """Slice a ZIP without the knowledge-base worker, persistence, or embedding services."""
    project, version, library = project.strip(), version.strip(), library.strip() or 'code-projects'
    if not project or len(project) > 100 or not version or len(version) > 32 or len(library) > 128:
        raise ValueError('工程名称必填（≤100字符），版本必填（≤32字符）')
    if job_id is None:
        job_id = uuid.uuid4().hex
    elif not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('无效任务编号')

    sources, skipped = inspect_archive(content, encoding)
    quality_files, hashes = {}, {}
    for path, source in sources.items():
        _, report = prepare_text(source, code=True)
        if report['sha256'] in hashes:
            report['duplicate_of'] = hashes[report['sha256']]
            report['warnings'].append('与另一文件内容相同，保留各自路径与引用身份')
        hashes.setdefault(report['sha256'], path)
        quality_files[path] = report
    engines = {source_engine(path) for path in sources}
    options = dict(
        project=project, version=version, library=library, encoding=encoding,
        scope=hashlib.sha256(f'{library}\0{project}\0{version}'.encode()).hexdigest(),
        files=list(sources), skipped=skipped, tags=list(tags or []),
        engine=' + '.join(name for engine, name in ENGINE_NAMES.items() if engine in engines),
        quality_report=dict(files=quality_files, accepted=len(sources),
                            quarantined=sum(bool(item.get('quarantined')) for item in skipped)),
    )

    with TemporaryDirectory(prefix='zip-cut-') as temporary:
        root = Path(temporary).resolve()
        for relative, source in sources.items():
            path = root / 'source' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.encode('utf-8'))
        raw = build_source_graph(root, options['files'])
        result = adapt_graph(raw, root, options, job_id)
    return dict(job_id=job_id, sources=sources, options=options, result=result)
