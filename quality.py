"""Normalize and validate the text of a source file."""

import hashlib
import re


def prepare_text(text, *, code=False):
    warnings = []
    normalized = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if normalized != text:
        warnings.append('已统一 BOM / 换行符，保留行序与代码缩进')
    if not normalized.strip():
        raise ValueError('没有有效文本内容')
    controls = [c for c in normalized if (ord(c) < 32 and c not in '\n\t') or c in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069']
    if controls:
        raise ValueError('包含二进制、非法控制字符或不可见方向控制符')
    broken = normalized.count('\ufffd')
    if broken:
        if broken >= 3 and broken / len(normalized) > .02:
            raise ValueError('文本疑似编码损坏，请确认编码后重新上传')
        warnings.append('含 Unicode 替换字符，请复核源文件编码')
    if re.search(r'(?m)^\s*(?:<<<<<<<\s|>>>>>>>\s)', normalized):
        if code:
            raise ValueError('含未解决的版本合并冲突标记')
        warnings.append('含合并冲突样式标记，请核对是否为文档示例')
    if any(len(line) > 20000 for line in normalized.splitlines()):
        warnings.append('含超长文本行，建议复核生成内容')
    return normalized, dict(sha256=hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
                            lines=len(normalized.splitlines()), warnings=warnings)
