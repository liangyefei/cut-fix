"""Find JCL job and step ranges without interpreting the job's data or commands."""

from dataclasses import replace
import re

from .source_blocks import SourceBlock, SourceReference


# JCL statement fields end in column 71. Retain sequence numbers in the slices.
# https://www.ibm.com/docs/en/zos/3.1.0?topic=statements-jcl-statement-fields
CARD = re.compile(r'^//(?P<name>[A-Z0-9@$#][A-Z0-9@$#.-]*)?[ \t]+'
                  r'(?P<op>[A-Z][A-Z0-9]*)(?:[ \t]+(?P<args>.*)|$)', re.I | re.ASCII)
KINDS = {'JOB': 'module', 'PROC': 'module', 'EXEC': 'function',
         'PEND': 'section', 'IF': 'section', 'ELSE': 'section', 'ENDIF': 'section'}
FRAGMENT_OPS = {'DD', 'SET', 'INCLUDE', 'JCLLIB', 'OUTPUT', 'EXPORT', 'SCHEDULE', 'COMMAND', 'CNTL', 'ENDCNTL'}
OPERATIONS = KINDS.keys() | FRAGMENT_OPS
IN_STREAM = re.compile(r'^(\*|DATA)(?=$|[ \t,])', re.I)
DLM = re.compile(r"'(?:''|[^'])*'|,DLM=('(?:''|[^'])*'|[^,'\s]+)", re.I)
QUOTED_CONTINUATION = '//' + ' ' * 13  # The literal continues in column 16.
THEN = re.compile(r'\bTHEN\b', re.I)
BANNER = re.compile(r'[*=_\- ]{3,}')


def _operand(args: str, quoted: bool = False) -> tuple[str, bool]:
    # An unquoted blank starts the comment field. A doubled apostrophe toggles twice.
    for i, char in enumerate(args):
        if char == "'":
            quoted = not quoted
        elif not quoted and char in ' \t':
            return args[:i], False
    return args, quoted


def _statement_tail(lines: list[str], index: int, args: str, op: str) -> tuple[int, str]:
    operands, quoted = (args, False) if op == 'IF' else _operand(args)
    last_card = lines[index - 1]
    pending = not THEN.search(operands) if op == 'IF' else quoted or operands.endswith(',')
    while pending and index < len(lines):
        continuation = lines[index][:71]
        if continuation.startswith('//*'):
            index += 1
            continue
        if not continuation.startswith(('// ', '//\t')) or not continuation[2:].strip():
            break
        if quoted:
            if not continuation.startswith(QUOTED_CONTINUATION):
                break
            part, quoted = _operand(continuation[15:], quoted=True)
        else:
            if (card := CARD.match(continuation)) and card['op'].upper() in OPERATIONS:
                break
            tail = continuation[2:].strip() if op == 'IF' else continuation[2:].lstrip()
            part, quoted = (' ' + tail, False) if op == 'IF' else _operand(tail)
        operands += part
        last_card = lines[index]
        index += 1
        pending = not THEN.search(operands) if op == 'IF' else quoted or operands.endswith(',')
    # Column 72 marks a continued comments field. Its text may start with an
    # operation word, so handle it before looking for another statement.
    while not pending and last_card[71:72].strip() and index < len(lines):
        if not lines[index].startswith(('// ', '//\t')) or not lines[index][2:71].strip():
            break
        last_card = lines[index]
        index += 1
    return index, operands


def _delimiter(operands: str) -> str:
    value = next((match[1] for match in DLM.finditer(operands) if match[1]), '')
    delimiter = value[1:-1].replace("''", "'").replace('&&', '&') if value.startswith("'") else value
    return delimiter or '/*'


def _step_title_start(lines: list[str], first: int, statement: int) -> int:
    """Find the last descriptive comment group immediately before EXEC."""
    def body(index: int) -> str:
        return lines[index][3:71].strip()

    end = statement
    while end > first and not body(end - 1):
        end -= 1
    if end > first and BANNER.fullmatch(body(end - 1)):
        end -= 1
    start = end
    while start > first and body(start - 1) and not BANNER.fullmatch(body(start - 1)):
        start -= 1
    if start == end:
        return statement
    return start - 1 if start > first and BANNER.fullmatch(body(start - 1)) else start


def split_jcl(source: str) -> list[SourceBlock]:
    lines = source.splitlines()
    blocks: list[SourceBlock] = []
    fragment = False
    delimiter = None
    end_at_card = False
    in_control = False
    comments = None
    job = proc = None
    branches: list[tuple[SourceReference, ...]] = []
    job_branches: list[tuple[SourceReference, ...]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if delimiter is not None:
            if delimiter and line.startswith(delimiter):
                delimiter = None
                continue
            if not (end_at_card and line.startswith('//')):
                continue
            delimiter = None

        if line.startswith('//*') and not in_control:
            if comments is None:
                comments = index - 1
            continue
        preceding_comments, comments = comments, None
        if not in_control and line[:71].rstrip() == '//':
            job = proc = None
            branches, job_branches = [], []
            continue
        card = CARD.match(line[:71])
        if not card:
            continue
        op = card['op'].upper()
        if in_control:
            if op == 'ENDCNTL':
                in_control = False
            continue
        if op not in OPERATIONS:
            continue
        statement = index
        index, operands = _statement_tail(lines, index, card['args'] or '', op)
        if op in FRAGMENT_OPS:
            fragment = True
        if op == 'CNTL':
            in_control = True
        if op == 'DD' and (mode := IN_STREAM.match(operands)):
            delimiter, end_at_card = _delimiter(operands), mode[1] == '*'
        if op not in KINDS:
            continue

        title = card['name'] or op
        reference_title = card['name'] or ''
        if op == 'IF':
            then = THEN.search(operands)
            reference_title = operands[:then.end()] if then else operands
        ref = SourceReference(op, reference_title, statement, index)
        if op == 'JOB':
            job, proc = ref, None
            branches, job_branches = [], []
        if op == 'PROC':
            proc = ref
            job_branches, branches = branches, []
        if op in {'ELSE', 'ENDIF'} and branches:
            # Both closing cards attach to the matching IF, even after ELSE.
            branches[-1] = branches[-1][:1]
        context = tuple(scope for scope in (job, proc) if scope is not None and scope != ref)
        context += tuple(scope for branch in branches for scope in branch)
        start = statement
        if op == 'EXEC' and preceding_comments is not None:
            start = _step_title_start(lines, preceding_comments, statement - 1) + 1
        parent = context[-1].start_line if context else 0
        blocks.append(SourceBlock(op, title, KINDS[op], start, index, statement, parent,
                                  context if op == 'EXEC' else ()))
        if op == 'IF':
            branches.append((ref,))
        elif op == 'ELSE' and branches:
            branches[-1] += (ref,)
        elif op == 'ENDIF' and branches:
            branches.pop()
        elif op == 'PEND':
            proc = None
            branches, job_branches = job_branches, []

    if not blocks:
        return [SourceBlock('FRAGMENT', 'JCL 片段', 'section', 1, len(lines))] if fragment else []
    prefix = [SourceBlock('', '文件开头', 'section', 1, blocks[0].start_line - 1)] if blocks[0].start_line > 1 else []
    return prefix + [replace(block, end_line=end - 1) for block, end in
                     zip(blocks, [next_block.start_line for next_block in blocks[1:]] + [len(lines) + 1])]
