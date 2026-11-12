"""Write the slicing result of a ZIP archive to a JSON file."""

import argparse
import json
from pathlib import Path

from .zip_cutter import MAX_UPLOAD, cut_zip


def main() -> None:
    parser = argparse.ArgumentParser(description='切分包含 COBOL、Copybook、APS 或 JCL 的工程 ZIP')
    parser.add_argument('archive', type=Path, help='输入 ZIP 路径')
    parser.add_argument('-o', '--output', required=True, type=Path, help='输出 JSON 路径')
    parser.add_argument('--encoding', choices=('utf-8', 'gb18030'), default='utf-8')
    parser.add_argument('--project', help='工程名称，默认使用 ZIP 文件名')
    parser.add_argument('--version', default='v1.0')
    parser.add_argument('--library', default='code-projects')
    args = parser.parse_args()
    try:
        if args.archive.resolve() == args.output.resolve() or (
            args.output.exists() and args.archive.samefile(args.output)
        ):
            raise ValueError('输出路径不能与输入 ZIP 相同')
        with args.archive.open('rb') as archive:
            content = archive.read(MAX_UPLOAD + 1)
        payload = cut_zip(content, project=args.project or args.archive.stem,
                          version=args.version, library=args.library, encoding=args.encoding)
        with args.output.open('w', encoding='utf-8', newline='\n') as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write('\n')
    except (ValueError, OSError) as exc:
        parser.exit(1, f'切分失败：{exc}\n')
    summary = payload['result']['summary']
    print(f'已切分 {summary["files"]} 个文件，共 {summary["chunks"]} 个切片。结果已写入 {args.output}')


if __name__ == '__main__':
    main()
