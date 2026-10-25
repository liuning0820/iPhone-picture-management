# MOV 文件跳过能力调查报告

## 总结答案

当前程序**不能通过现有命令行参数跳过 MOV 文件类型**。`iphone_copy.py`
没有按扩展名排除的参数；运行复制时，匹配文件夹内的 MOV 会和 JPG 等其他
文件一起进入复制清单，因此示例中的 `CTVE0260.MOV` 会被尝试复制。

如果要实现该能力，最小改动是在采集源文件清单时增加一个大小写不敏感的
扩展名排除参数，例如 `--exclude-extension .MOV`，并在加入 `SourceFile`
之前过滤。实现后示例命令可以写成：

```powershell
python iphone_copy.py 202607 `
  --destination "$env:USERPROFILE\OneDrive" `
  --exclude-extension .MOV
```

上面的参数目前尚不存在，直接执行会被 `argparse` 报未知参数错误；它是
实现建议，不是当前可用命令。

## 证据

### 命令行参数

`iphone_copy.py:717-752` 的 `build_parser()` 注册了当前 CLI 参数：

- 位置参数 `period`，用于选择 `YYYY` 或 `YYYYMM` 文件夹。
- `--destination`、`--device-name`、`--storage-name`。
- `--overwrite`、`--timeout` 和 `--list-only`。

其中没有 `--exclude-extension`、`--skip-extension` 或其他按文件扩展名
排除的参数。`iphone_copy.py:803-810` 的 `main()` 直接使用该 parser，因而
未注册的排除参数不会被接受。

### 文件筛选逻辑

`iphone_copy.py:459-513` 的 `collect_source_selection()` 只按顶层文件夹
名称筛选。`Period.parse()` 和 `Period.matches()`（`iphone_copy.py:25-44`）
负责匹配 `YYYYMM_*` 文件夹或某年份的有效月份文件夹；它们不检查文件扩展名。

在 `collect_source_selection()` 内部的 `walk()`（`iphone_copy.py:480-510`）
会递归遍历每个匹配文件夹：

1. 子项目是文件夹时继续递归。
2. 子项目不是文件夹时，读取大小并创建 `SourceFile`。
3. 该文件随后加入 `files`，没有任何 `.MOV`、`.JPG` 或其他扩展名判断。

`copy_selection()`（`iphone_copy.py:563-635`）遍历已建立的
`selection.files` 并逐个调用后端复制，所以复制阶段没有第二层文件类型
过滤。`verify_selection()`（`iphone_copy.py:653-683`）也按完整源清单验证；
因此排除逻辑必须发生在建立源清单时，才能让复制和最终校验保持一致。

### 现有测试

`tests/test_iphone_copy.py:93-122` 的选择测试包含 `IMG_3.MOV`，但它位于
未匹配的 `202611_a` 文件夹，因此该测试只验证了文件夹筛选，不是扩展名
排除功能。

`tests/test_iphone_copy.py:146-183` 的复制测试明确将 `IMG_2.MOV` 放入
选中的 `SourceSelection`，并断言后端复制调用包含该文件。这说明当前行为
是把 MOV 作为普通文件复制，而不是自动跳过。

### README 用法

`README.md:1-8` 描述工具会复制照片和视频，并保留文件夹结构；其中没有
按类型排除的功能说明。

`README.md:37-76` 的使用示例只覆盖默认目标目录、指定目标目录、
`--list-only` 和 `--help`。没有排除 MOV 的示例，也没有其他可替代的排除
参数。

`README.md:110-124` 的故障排查内容解释了视频通过 Windows MTP 复制可能
超时，并建议增加 `--timeout`；它没有提供跳过视频的当前用法。

### 项目验证方式

仓库没有找到 `AGENTS.md`、`CONTRIBUTING.md`、`.kiro/steering` 文件或
Python 构建配置。依赖文件 `requirements.txt` 仅固定了
`pywin32==311`。README 在 `README.md:137-143` 指定单元测试命令：

```powershell
python -m unittest discover -s tests -v
```

本次调查未修改源代码、测试、README 或配置，也未提交或重启任何服务。
调查开始时 `git -C C:\Users\toby0\github\iPhone-picture-management
status --short` 显示工作区已有改动：`.kiro/settings/lsp.json` 被添加、
`README.md` 被修改，并存在未跟踪的 `.agents/`；这些改动不是本次调查造成
的源代码修改。

## 结论与建议

结论是：当前没有按扩展名跳过 MOV 的命令行能力。`--list-only` 只显示匹配
结果，不会改变文件清单；`--timeout` 只改变单个文件的等待时间，也不会
排除 MOV。

建议采用一个可重复使用的 `--exclude-extension EXT` 参数，而不是为 MOV
硬编码专用开关。实现时应：

1. 在 `build_parser()` 中增加可重复指定的参数，或接受逗号分隔值；推荐
   可重复参数，以便用户写 `--exclude-extension .MOV --exclude-extension .MP4`。
2. 统一处理输入格式，自动补齐前导点，并使用 `casefold()` 比较扩展名，
   使 `.MOV`、`.mov` 和 `.Mov` 都能被排除。
3. 在 `collect_source_selection()` 的 `walk()` 中、读取文件大小和加入
   `SourceFile` 之前执行过滤。这样被排除的文件既不会复制，也不会进入
   `verify_selection()` 的期望清单。
4. 为 `tests/test_iphone_copy.py` 增加选择测试，覆盖大小写不敏感的 MOV
   排除、非 MOV 文件保留，以及不传参数时维持当前“复制所有文件”的行为。
5. 在 `README.md` 的 Usage 和 Features 部分说明参数，并加入类似以下
   的 PowerShell 示例：

   ```powershell
   python iphone_copy.py 202607 `
     --destination "$env:USERPROFILE\OneDrive" `
     --exclude-extension .MOV
   ```

实现后应运行仓库既有测试命令，并确认输出的源文件数量和总大小已经不再
包含被排除的 MOV 文件。