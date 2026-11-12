# ZIP 工程源码切分与内网接入

这份目录从当前 `knowledge-base` 工作区抽取了 ZIP 校验、源码规范化、结构解析、切片与源码定位逻辑。可以独立试跑，也可以接到内网知识库现有的上传和后台任务。独立入口返回规范化源码、工程页面展示的结构切片、图谱关系和处理报告。现有项目在发布阶段还会从结构切片生成用于向量检索的子切片，由宿主的 `publish` 和 `representations.encode_many` 继续处理。

## 支持的文件

| ZIP 内的扩展名 | 解析方式 | 主要切分单位 |
| --- | --- | --- |
| `.cbl`、`.cob`、`.cobol` | CodeGraph 1.6.2 | CodeGraph 识别的过程、变量和常量 |
| `.cpy` | CodeGraph 1.6.2 | Copybook 中识别出的变量、常量等结构 |
| `.aps` | APS 原文解析 | 程序入口、段落、声明、记录、报表等结构 |
| `.jcl` | JCL 原文解析 | JOB、PROC、EXEC 步骤、条件分支及独立片段 |

扩展名匹配忽略大小写，一个 ZIP 可以混合以上类型。ZIP 条目经过校验后才会把可用源码写入解析目录。`.git`、`.codegraph`、`node_modules`、`__macosx` 和其他后缀的文件计入跳过清单。处理原始 ZIP 的留存时，宿主保存上传字节即可保留这些条目。

## 输入边界

- 单个 ZIP 最大 50 MiB，条目声明的解压总量最大 200 MiB，最多 5000 个条目。单个支持类型的源码在解码前和转为 UTF-8 后各有 1 MiB 上限。文件路径最多 240 个字符、20 个路径分量，路径分量包含文件名。
- 绝对路径、父目录跳转、危险的 Windows 路径名、符号链接、加密条目和大小写冲突路径会使整个 ZIP 失败。损坏或使用不支持压缩方式的 ZIP 也会失败。
- 默认按 UTF-8 解码，可为整个 ZIP 指定 GB18030。删除开头的 BOM，将 CRLF 和单独的 CR 统一为 LF。源码缩进和行序保留。解码失败、空文本、非法控制字符、疑似编码损坏和未解决的合并冲突会隔离对应文件，原因在 `options.skipped`。可用源码为空时工程失败。

## 切片边界

### COBOL 与 Copybook

CodeGraph 扫描源码，返回节点和起止行。项目适配层只为 `function`、`variable`、`constant` 节点生成结构切片，按行号截取规范化后的源码。选中范围内的注释、空行和缩进随切片一起保留。文件头注释、`FILE-CONTROL`、`COPY` 等内容如果落在所有选中范围之外，就只保留在源码中。父级结构和子级结构的范围也可能重叠，所以 COBOL 切片顺次拼接可能既有缺行，也有重复行。完整源码以 ZIP 或 `sources[path]` 为准。

模块、文件、字段、`COPY` 等其他已识别节点可作为图谱信息输出，图谱节点是否存在与是否生成检索切片是两套规则。解析器不会为已经产生结构切片的文件逐段补齐未覆盖的行。

### APS

- 每个文件从标准 APS 固定列（关键字从第 8 列起）和部分导出格式（关键字从第 4 列起）中选择识别结果较多的一种，平局优先使用标准列。变更标记和尾部序号保留在原文里。
- 按 `PROC`、`NTRY`、`PARA`、`DECL`、`DPAR`、`WS`、`LK`、`IO`、`FD`、`REC`、`RED`、`MOCK` 等已知关键字识别边界。在过程上下文中也识别部分以段名加句点表示的 COBOL/2 段落和 SECTION。完整识别表在 [aps.py](aps.py)。
- 块通常从当前边界延伸到下一边界前一行。符合段落归属规则的前置 `/*` 注释可以并入下一段落。有实际内容的文件开头单独保留。只有标记与空行的 `WS`、`LK` 标头可随首条数据定义一起切分。
- 文件定义、记录、报表组等可携带 `aps_context`，供展示或检索时引用相关行。APS 块直接引用原文行号。宏展开和生成 COBOL 不在这条切分路径中。

### JCL

- 以 `JOB`、`PROC`、`EXEC`、`PEND`、`IF`、`ELSE`、`ENDIF` 为结构边界。一个块延伸到下一结构块之前。紧邻 `EXEC` 的标题型 `//*` 注释可以进入步骤切片，`statement_line` 仍指向实际 `EXEC` 卡。
- `DD`、参数和注释的续行、内联数据和控制语句留在所在范围内。识别 `DD *`、`DD DATA`、默认分隔符和字面量 `DLM`，在内联数据中按对应终止规则避免把数据误作步骤。语句字段按第 1–71 列匹配，第 72 列可标记注释续行，序号等列仍保留在切片中。
- `jcl_context` 记录 EXEC 所属的 JOB、PROC 或 IF/ELSE 分支。只有 DD、SET、INCLUDE 等受支持操作的独立片段整文件输出。外部 PROC 和 INCLUDE 不展开，JCL 作业不执行。完整识别表在 [jcl.py](jcl.py)。

### 回退与坐标

- 某文件完全没有结构切片时，按每 100 行回退，末段可少于 100 行。回退切片的 `node_id` 为空，文件报告标记 `structural_chunks: false`。整个工程都没有识别到有效结构节点时，任务返回解析错误。有效结构指 CodeGraph 的模块、过程、变量或字段节点，或 APS/JCL 的有效结构块。
- `start_line`、`end_line` 从 1 开始，包含首尾两行。`start_offset`、`end_offset` 是规范化文本的字符偏移，从 0 开始，右侧不包含。每个切片都满足 `chunk["text"] == sources[path][start_offset:end_offset]`。结构块保持完整，未按字符数或模型 token 数再次切分。
- `statement_line` 在存在时指向结构语句所在行，可用于查看代码时跳转。上下文元数据保留引用范围，不会拼接进切片的 `text`。

## 独立运行

使用 Python 3.11 或更高版本。APS/JCL 工程仅用 Python 标准库。ZIP 内含 COBOL 或 Copybook 时需要 CodeGraph 1.6.2 及配套 Node.js，原项目部署使用 Node.js 25.8.2。以下命令在 `cut` 的上一级目录运行。

```sh
# 含 COBOL / Copybook 时安装，锁文件会固定版本
npm ci --prefix cut/tools/codegraph

python -B -m cut test-jcl.zip -o jcl-result.json
python -B -m cut test-cobol.zip -o cobol-result.json --encoding utf-8
```

如果服务的 PATH 无法找到 Node，设置 `KB_CODEGRAPH_NODE` 为可执行文件的绝对路径。隔离内网使用内网 npm 镜像、预置 npm 缓存，或将按部署系统和架构安装的依赖随部署包放入 `tools/codegraph/node_modules`。依赖包含平台包，保留 npm optional dependencies。CodeGraph 会在可写的解析目录下生成 SQLite 索引，单次运行超时为 600 秒。独立入口使用系统临时目录，运行结束会清理工作文件。如果默认临时目录不可写，启动前将 `TEMP` 和 `TMP`（Linux 可用 `TMPDIR`）指向可写目录。

也可以从 Python 调用。

```python
from pathlib import Path

from cut import cut_zip

payload = cut_zip(Path("test-jcl.zip").read_bytes(), project="test-jcl", version="v1.0")
chunks = payload["result"]["chunks"]
sources = payload["sources"]
```

输出的 `job_id` 标识本次运行。`options` 包含文件清单、解析引擎、跳过原因、源码质量报告和工程范围。`result` 包含 `chunks`、`nodes`、`edges`、`unresolved`、`files`、`summary`、`engine_version`。查看某个文件是否成功产生结构切片时，以 `result.files` 中的 `structural_chunks` 和 `errors` 为准。独立模式下可按切片的 `path` 从 `sources` 读取全文。图谱里的 `source_path` 沿用宿主的存储键格式，供集成时定位快照。JSON 含工程源码，供内网验收。

## 合并到内网仓库

先在内网仓库找到工程 ZIP 的上传入口、后台解析、切片和发布代码。下表使用本地项目的函数名。内网版本有改名时，按调用阶段找到对应位置。默认采用下表接法，有差异时按后面的冲突表逐项决定。

1. 将本目录的 Python 文件及 `tools/codegraph` 放到内网仓库的 `backend/app/ingest/cut/`。需要 COBOL 时，在仓库根目录运行 `npm ci --prefix backend/app/ingest/cut/tools/codegraph`，或在部署构建时准备相同依赖。
2. 在 `backend/app/ingest/code_project.py` 中从相对模块 `.cut.zip_cutter` 导入所需符号，按下表接入。保留宿主的上传鉴权、后台队列、状态流转、对象存储和发布事务。宿主仍负责保存 `upload.zip` 和按相对路径写入 `root/source/`。传给切分函数的 `root` 是工程快照的绝对路径，`job_id` 使用本次上传的现有任务 ID。
3. 对照宿主的发布逻辑检查切片字段是否一路传到数据库、图谱、检索和源码跳转。若测试通过旧模块替换 `run_codegraph` 或 `MAX_SOURCE`，接入后把测试替换的目标移到实际引用这些符号的模块，保留原有测试断言。

| 阶段 | 默认接法 | 调用方需要保留的行为 |
| --- | --- | --- |
| 上传与预检 | `create_project` 调用 `inspect_archive(content, encoding)` | 使用返回的 `sources` 和 `skipped` 建立清单。上传接口的读取上限与预检一致。本地路由读取 `projects.MAX_UPLOAD`，替换时继续导出该常量或同步修改引用。 |
| 解析 | `raw = build_source_graph(root, options["files"])` | 输入清单来自 ZIP 预检，保留解析阶段的状态和失败信息。 |
| 切片 | `result = adapt_graph(raw, root, options, job_id)` | `options` 至少有 `files`、`project`、`version`、`scope`、`engine`、`skipped`，沿用已有的 `library` 和 `tags`。宿主的 `result.json` 写入这里的 `result`。独立命令输出的外层还含 `sources` 等验收数据。 |
| 发布 | 继续走宿主的 `publish(job_id, result, options)` | 保留项目和版本身份、存储键、索引与回滚。文件类型显示、`lang` 及上下文处理与实际路由使用同一套 `source_engine`、`SOURCE_SPLITTERS`、`ENGINE_NAMES`。保存可选字段 `statement_line`、`aps_context`、`jcl_context`。 |

本目录的 `prepare_text` 用于源码校验。宿主的 `quality.py` 还提供文档质量检查和向量校验，继续保留该模块。部署后，已有工程的历史切片需要重新上传或走宿主重建流程才会更新。

### 冲突取舍

先用同一个代表性 ZIP 分别跑内网现有链路和 `cut`，记录受影响文件的路径、边界、定位字段和报错。对比时用 `path`、标题和行范围定位差异。任务 ID 不同时，生成的节点和切片 ID 本来就不同。

| 发现的冲突 | 取舍 |
| --- | --- |
| APS 或 JCL 走通用行窗、按字符分片，或 APS 先转成 COBOL | 新入库默认采用本包对应的原文结构规则。每个文件只发布一套工程结构切片。保留内网已有且能用样例证明的方言规则，合并到该类型解析器，再验证原文行号。 |
| 内网 COBOL/Copybook 有专门的解析器、节点选择或补齐规则 | 先保留已经通过内网验收的 COBOL 路径。比较本包 CodeGraph 1.6.2 的段落、字段、COPY/CALL 和缺行情况。确认目标就是采用本包行为，且代表样本没有丢失必需信息，再切换。若暂时保留内网 COBOL，就在工程解析入口按扩展名分流，将各引擎的结果适配给同一发布链路。 |
| 宿主会按模型长度切子片 | 保留发布阶段的子切片和向量流程。工程结构切片的 `text` 与坐标仍来自整段原文。上下文标题可以用于检索，不能拼进用于定位的切片正文。 |
| ZIP 限额、编码、路径校验或额外支持类型不同 | 保留内网更严格的校验和已验证的类型与编码。统一上传预检、文件清单、路由与展示，额外类型继续走内网已有处理。修改编码或换行策略时，源码预览与坐标计算必须读同一份规范化文本。 |
| 字段名、状态、对象键、索引方式或历史数据不同 | 沿用内网对外契约，在适配层映射结果。保留源码路径、行范围、字符偏移、语句行和上下文引用的含义，并检查引用到原文的跳转。失败任务沿用内网的回滚和重试流程。 |
| 内网已经部署 CodeGraph，版本或安装路径不同 | 核对实际版本、桥接脚本以及 SQLite 的 `nodes`、`edges`、`files`、`unresolved_refs` 表。若复用内网桥接，更新调用路径及实际版本报告。用目标部署环境验证读表、混合 ZIP 和超时行为后再启用。 |

内网要保留自己的 COBOL 解析时，可以把其结构输出适配成 `nodes`、`edges`、`files`、`unresolved_refs` 四项，再调用 `append_source_graph(raw, root / "source", aps_jcl_paths)` 加入 APS/JCL 结构，最后交给 `adapt_graph`。如果内网 COBOL 依赖自己的切片规则，例如补齐 CodeGraph 范围外的行，应在宿主的结果组装阶段保留这些 COBOL 切片。直接传给本包的 `adapt_graph` 会按本包规则重新选取 COBOL 节点生成切片。

如果样例不足以判断哪个边界符合内网业务预期，先保留受影响文件类型的现行入口，记录具体文件和差异供负责人确认。其他能够验证的类型可以先完成接入。

### 合并后的验收

1. 用测试工程名或隔离知识库分别上传 APS、JCL、COBOL/Copybook 和混合 ZIP，确认上传、解析、切片、发布均完成。出现失败时，用任务阶段区分解析错误和发布或向量错误。
2. APS 检查段落注释、文件与记录归属，JCL 检查 `EXEC` 与其 DD、续行和内联数据的范围。两者都按 `statement_line` 和上下文引用回到正确原文。COBOL 检查段落、数据项、COPY/CALL 关系及全文预览。
3. 抽查 `source[start_offset:end_offset] == chunk["text"]`，并用 `"".join(source.splitlines(keepends=True)[start_line - 1:end_line])` 再核对行范围。确认回退标记、跳过原因和未解析引用仍能看到。样本切片数变化时记录对应文件及边界原因。
4. 验证解析或发布失败时，上一版仍可查看和检索，重试产生独立任务。同工程同版本的新任务成功发布后，再验证内网现有的替换与索引行为。按测试环境通过后的版本发布，历史工程按需单独重建。

### 可直接交给内网 AI 的任务

```text
请读取 cut/README.md 和 cut 中的源码，把 ZIP 工程结构切分接入当前内网仓库。
先定位上传预检、任务解析、切片、发布和源码查看的现有入口。
按 README 的冲突表对照旧行为和样例，然后完成可验证的接入。
默认对 APS/JCL 启用原文结构切分。COBOL/Copybook 有冲突时，
用内网样例比较覆盖和图谱关系，再决定是否采用本包的 CodeGraph 路径。
沿用宿主的鉴权、任务状态、存储、发布及检索链路。保留切片的源码定位。
在测试环境验证单类型、混合 ZIP、回退和失败重试。
交付改动文件、各类型采用的切分规则、冲突与取舍依据、测试结果，以及待确认的样例。
```

| 交付文件 | 来源与用途 |
| --- | --- |
| [zip_cutter.py](zip_cutter.py) | 从 `backend/app/ingest/code_project.py` 抽取 ZIP 校验、引擎路由、图谱适配及切片。独立入口用临时目录替代项目存储，并在读取完 CodeGraph SQLite 索引后关闭连接。 |
| [aps.py](aps.py)、[jcl.py](jcl.py)、[source_blocks.py](source_blocks.py) | 原工程的原文切分器与行范围结构。 |
| [quality.py](quality.py) | 原工程的 `prepare_text` 源码校验与规范化函数。 |
| [tools/codegraph](tools/codegraph/) | 原工程的 CodeGraph 桥接脚本和依赖锁文件。 |
| [__init__.py](__init__.py)、[__main__.py](__main__.py) | 独立试跑的 Python 和命令行入口。 |

## 本地验收基线

以下结果与当前工作区的原项目切分逻辑对照验证。每个切片的行号和字符范围均可定位到对应的规范化原文。

| 样本 | 可用文件 | 切片 |
| --- | ---: | ---: |
| 已上传的 APS 工程 ZIP | 3 | 77 |
| `test-jcl.zip` | 38 | 139 |
| `test-cobol.zip` | 78 | 1524 |

另外验证了三类文件混装、GB18030、扩展名大小写，以及未识别文件的 100 行回退。交付目录只含代码与说明。
