"""Source ranges for APS Program Painter and Specification Editor blocks."""

import re

from .source_blocks import SourceBlock, SourceReference


# APS 5 Reference, Program Painter keywords and pass-through parameters:
# https://www.microfocus.com/documentation/aps50/rfitok.htm#Keywords
PROCEDURES = {'PROC', 'NTRY', 'PARA', 'DECL', 'DPAR'}
RECORDS = {'01', 'REC', 'LK01', 'WS01', 'CA', 'CA05', 'CADS'}
NAMED = {'PARA', 'DPAR', 'STUB', 'FD', 'SD', 'REC', 'RED', 'MOCK', 'DS', '++'}
KEYWORDS = PROCEDURES | RECORDS | NAMED | {
    'REM', 'SPNM', 'IO', 'WS', 'LK', 'OPT', 'FRFM', 'SQL', 'CICS', 'IDCS', 'IDSS',
    'SYM1', 'SYM2', 'SYEN', 'SYIO', 'SYDD', 'SYFD', 'SYWS', 'SYLT', 'SYLK', 'SYRP', 'SYBT',
}
WORD = re.compile(r'[A-Z0-9@$#][A-Z0-9@$#_-]*(?=$|[\s.(,])', re.IGNORECASE | re.ASCII)
COBOL_HEADER = re.compile(r'(?:PROCEDURE\s+DIVISION\b|(?:END\s+)?DECLARATIVES\s*\.)', re.IGNORECASE)
COBOL_LABEL = re.compile(r'([A-Z0-9@$#][A-Z0-9@$#_-]{0,23})(\s+SECTION(?:\s+\d+)?)?\s*\.', re.IGNORECASE | re.ASCII)
COBOL_STANDALONE = {'CONTINUE', 'ELSE', 'EJECT', 'EXIT', 'GOBACK', 'SKIP1', 'SKIP2', 'SKIP3'}
SCOPE_END = re.compile(r'END-(?:ACCEPT|ADD|CALL|COMPUTE|DELETE|DISPLAY|DIVIDE|EVALUATE|EXEC|IF|JSON|'
                       r'MULTIPLY|PERFORM|READ|RETURN|REWRITE|SEARCH|START|STRING|SUBTRACT|UNSTRING|WRITE|XML)')
PROCEDURE_CONTEXT = PROCEDURES | {'OPT', 'STUB', 'COBOL-PARA', 'COBOL-SECTION'}
PASSTHROUGH = {'FRFM', 'SQL', 'CICS', 'IDCS', 'IDSS'}
EXPORT_PREFIX = re.compile(r'(?:[0-9]{2}| {2}) ')
TRAILING_SEQUENCE = re.compile(r'(?<!\S)[0-9]{8}[ \t]*$')
PARAGRAPHS = {'PARA', 'DPAR', 'COBOL-PARA', 'COBOL-SECTION'}
DATA_DEFINITIONS = RECORDS | {'DS', '++', 'SQL', 'FRFM'}
DATA_SECTIONS = {'WS', 'LK', 'FD', 'SD', 'RED'}
ENTRY_POINT = re.compile(r'\bENTRY\s+POINT\s*:?\s+([\w@$#-]+)', re.I)
SPACING = {'', 'SKIP1', 'SKIP2', 'SKIP3'}


def _code_area(line: str, column: int) -> str:
    # Sequence fields affect detection, never the original slice text.
    sequence = TRAILING_SEQUENCE.search(line, 72)
    return line[column:sequence.start() if sequence else 80]


def _header(line: str, column: int, in_procedure: bool = False) -> tuple[str, str] | None:
    # The Painter layout starts in column 8. Some exported members move the
    # four-column field after a two-digit change marker and a space instead.
    if not (line[6:7] == ' ' if column == 7 else EXPORT_PREFIX.match(line)):
        return None
    if COBOL_HEADER.match(line, column):
        return None
    code = _code_area(line, column)
    label = COBOL_LABEL.fullmatch(code.rstrip())
    if label and (name := label[1].upper()) not in KEYWORDS:
        # In APS COBOL/2, a name in column 8 can omit the PARA keyword.
        if in_procedure and name not in COBOL_STANDALONE and not SCOPE_END.fullmatch(name):
            return ('COBOL-SECTION' if label[2] else 'COBOL-PARA'), label[1]
        return None
    keyword = code[:4].strip().upper()
    return (keyword, code[4:].strip()) if keyword in KEYWORDS else None


def _title(keyword: str, body: str) -> str:
    if keyword in {'COBOL-PARA', 'COBOL-SECTION'}:
        return body
    if keyword == 'PROC':
        return f'PROC {body.partition("/*")[0].strip()}'.rstrip()
    if keyword == 'IO' and body.upper().startswith('SELECT '):
        body = body[7:].lstrip()
    if keyword in {'01', 'WS01', 'LK01'}:
        # A Report Writer group may have a TYPE clause in place of a name.
        if re.match(r'TYPE\s', body, re.IGNORECASE):
            return f'{keyword} {body.rstrip(".")}'
        if body.upper().startswith('COPY '):
            return f'{keyword} {body.rstrip(".")}'
    name = WORD.match(body)
    if name and keyword in NAMED | RECORDS:
        return name.group()
    if name and keyword == 'IO':
        return f'{keyword} {name.group()}'
    return keyword


def _paragraph_comment_start(lines: list[str], statement: int, lower: int, column: int, title: str) -> int:
    # A page break separates earlier notes from the documentation for this paragraph.
    index = statement - 1
    while index > lower and _code_area(lines[index - 1], column).strip().upper() in SPACING:
        index -= 1
    end = index
    while index > lower and _code_area(lines[index - 1], column).startswith('/*'):
        index -= 1
    entry_points = {match[1].upper() for line in lines[index:end] if (match := ENTRY_POINT.search(line))}
    if index == end or (entry_points and title.upper() not in entry_points):
        return statement
    # An unlabelled comment directly after executable code can be a tail
    # note. Only move it when separated by spacing or a page directive.
    if not entry_points and (index <= lower or
                             _code_area(lines[index - 1], column).strip().upper() not in SPACING | {'EJECT'}):
        return statement
    if not any(_code_area(line, column)[2:].strip() for line in lines[index:end]):
        return statement
    return index + 1


def split_aps(source: str) -> list[SourceBlock]:
    lines = source.splitlines()
    # Pick one layout for the entire member; the data or body columns in a
    # mixed export should not be interpreted as a second keyword field.
    column = max((7, 3), key=lambda start: sum(_header(line, start) is not None for line in lines))
    headers = []
    in_procedure = False
    for line_no, line in enumerate(lines, 1):
        if (header := _header(line, column, in_procedure)) is not None:
            keyword, body = header
            headers.append((line_no, keyword, body))
            if keyword not in PASSTHROUGH:
                in_procedure = keyword in PROCEDURE_CONTEXT
    if not any(keyword != 'REM' for _, keyword, _ in headers):
        return []

    starts = [statement for statement, _, _ in headers]
    collapsed = set()
    for i, (statement, keyword, body) in enumerate(headers):
        if keyword in PARAGRAPHS:
            starts[i] = _paragraph_comment_start(lines, statement, headers[i - 1][0] if i else 0,
                                                 column, _title(keyword, body))
        if keyword in {'WS', 'LK'} and not body and i + 1 < len(headers):
            next_statement, next_keyword, _ = headers[i + 1]
            if next_keyword in DATA_DEFINITIONS and all(not _code_area(line, column).strip()
                                                       for line in lines[statement:next_statement - 1]):
                starts[i + 1] = statement
                collapsed.add(i)

    blocks = []
    first = starts[0]
    if first > 1:
        if any(_code_area(line, column).strip() for line in lines[:first - 1]):
            blocks.append(SourceBlock('', '文件开头', 'section', 1, first - 1))
        else:
            starts[1 if 0 in collapsed else 0] = 1
    scope: tuple[SourceReference, ...] = ()
    input_output = {}
    for i, (statement, keyword, body) in enumerate(headers):
        title = _title(keyword, body)
        end = starts[i + 1] - 1 if i + 1 < len(headers) else len(lines)
        reference_title = '' if title == keyword else title.removeprefix('IO ') if keyword == 'IO' else title
        reference = SourceReference(keyword, reference_title,
                                    statement, statement if i in collapsed else end)
        context = ()
        if keyword == 'IO' and reference.title:
            input_output[reference.title.upper()] = reference
        if keyword in DATA_SECTIONS:
            if keyword in {'FD', 'SD'} and title.upper() in input_output:
                context = (input_output[title.upper()],)
            scope = (*context, reference)
        elif keyword == 'MOCK':
            context = tuple(ref for ref in scope if ref.keyword == 'RED')
            scope = (*context, reference)
        elif keyword in DATA_DEFINITIONS:
            context = scope
        else:
            scope = ()
        if i in collapsed:
            continue
        kind = 'function' if keyword in PROCEDURE_CONTEXT else 'variable' if keyword in RECORDS else 'section'
        if keyword == '01' and any(ref.keyword == 'RED' for ref in context):
            kind = 'section'
        parent = next((ref.start_line for ref in reversed(context) if ref.keyword in {'FD', 'SD', 'RED', 'MOCK'}), 0)
        blocks.append(SourceBlock(keyword, title, kind, starts[i], end, statement, parent, context))
    return blocks
