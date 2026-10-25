# Implementation Plan

本计划基于已阅读的 `iphone_copy.py`、`README.md`、
`tests/test_iphone_copy.py` 和 `requirements.txt` 制定。仓库没有发现
`AGENTS.md`、`CONTRIBUTING.md` 或 `.kiro/steering` 文件；现有项目测试命令是
`python -m unittest discover -s tests -v`，没有独立的 lint 或 type-check 配置。
实现只修改仓库内的实现、测试和 README，不创建 git commit 或推送。

- [ ] 1. 增加扩展名规范化函数和 CLI 参数解析。
      在 `iphone_copy.py` 中新增一个职责单一的规范化辅助函数，例如
      `normalize_skip_extensions(values: Iterable[str]) -> frozenset[str]`：去除
      可选前导点，使用 `casefold()` 统一大小写，并拒绝空扩展名；匹配值保留
      不带点的形式，避免把扩展名误当成文件名主体。`build_parser()` 增加固定名
      `--skip-extension`，使用 `action="append"` 支持重复传入并默认为空列表；在
      `run()` 中把解析结果规范化后传入清单构建流程。保持现有
      `--destination`、`--overwrite`、`--list-only` 和默认目标目录行为不变。
      
      Files: `iphone_copy.py`
      
      Verify: 运行 `python -m unittest discover -s tests -v`；现有测试通过，且后续
      CLI 解析测试能验证 `.MOV`、`MOV` 以及重复参数被正确接收和规范化。

- [ ] 2. 在源文件清单构建阶段按扩展名过滤，并记录可见的跳过信息。
      修改 `collect_source_selection()`，增加规范化扩展名集合参数；对每个非文件夹
      项目先用文件名最后一个后缀（而不是文件名主体）进行大小写不敏感比较，再决定
      是否加入 `SourceSelection.files`。扩展名匹配时同时支持用户输入 `MOV` 和
      `.MOV`，且只匹配 `photo.MOV` 的 `.MOV` 后缀，不匹配主体包含 `MOV` 的其他
      文件。为 `SourceSelection` 增加能供 CLI 展示的跳过文件记录或等价计数；被过滤
      的文件不能进入 `files`，因此既不会进入 `copy_selection()`，也不会调用
      `backend.copy_file()`。在 `run()` 的“源文件合计”附近输出跳过数量和所用扩展名
      （扩展名可按稳定顺序展示），`--list-only` 也必须显示该信息。不要改变
      `copy_selection()` 对目标文件“已存在且大小一致”时的现有跳过逻辑及其计数。
      
      Files: `iphone_copy.py`
      
      Verify: 运行 `python -m unittest discover -s tests -v`；新增选择测试应证明 MOV
      不出现在 `selection.files`，并且复制测试中的 fake backend 没有收到 MOV 的
      `copy_file()` 调用；现有目标文件已存在且大小一致的测试仍通过。

- [ ] 3. 补充命令行、规范化和过滤行为的单元测试。
      在现有 `tests/test_iphone_copy.py` 的 unittest 风格下增加测试，导入
      `build_parser`、规范化函数及更新后的清单 API。覆盖：单个
      `--skip-extension` 参数解析；重复参数 `.MOV` 与 `.MP4` 的解析；`MOV`、
      `.MOV`、大小写混合输入规范化为相同内部值；大小写混合文件名扩展名匹配；带有
      `MOV` 但扩展名不同的文件不被误过滤；包含 MOV 和 JPG 的 fake MTP 源只把 JPG
      放入清单，且 `copy_selection()` 的复制调用列表不包含 MOV。增加至少一个不传
      过滤参数的回归测试，确认原有清单和复制行为保持不变。
      
      Files: `tests/test_iphone_copy.py`
      
      Verify: `python -m unittest discover -s tests -v` 应全部通过；再运行
      `python -m py_compile iphone_copy.py tests/test_iphone_copy.py` 应成功且无语法
      错误。

- [ ] 4. 更新 README 的功能、用法和参数说明。
      在 `README.md` 的 Features 中说明可以按扩展名跳过源文件，并明确匹配不区分
      大小写、前导点可省略、参数可重复。新增 PowerShell 示例：
      `python .\\iphone_copy.py 202607 --destination "$env:USERPROFILE\\OneDrive"`
      配合 `--skip-extension .MOV`，并补充同时跳过多个扩展名的示例或参数说明。
      说明这些文件在源清单阶段被排除，不会启动 MTP 复制；命令输出会报告跳过数量，
      而“目标文件已存在且大小一致”的既有跳过逻辑仍独立保留。保持 README 现有英文
      风格和命令格式，不引入无关功能或外部文档链接。
      
      Files: `README.md`
      
      Verify: 运行 `python -m unittest discover -s tests -v`，确认文档对应的行为由
      测试覆盖；运行 `python -m py_compile iphone_copy.py tests/test_iphone_copy.py`
      确认代码仍可编译。人工复核 README 中 PowerShell 命令、参数名
      `--skip-extension` 和重复参数示例与最终 parser 行为一致。

- [ ] 5. 执行最终回归验证并检查改动范围。
      完成实现后从仓库根目录运行完整 unittest 和 Python 编译检查；如项目环境提供
      其他检查则仅运行仓库实际存在且与 Python 项目相关的命令。确认测试输出包含所有
      测试通过，确认过滤后的源文件数量/总大小和最终校验都只针对未跳过文件，并确认
      未修改项目外文件、未创建 commit。
      
      Files: `iphone_copy.py`, `tests/test_iphone_copy.py`, `README.md`
      
      Verify: `python -m unittest discover -s tests -v` 全部通过；
      `python -m py_compile iphone_copy.py tests/test_iphone_copy.py` 返回成功。
