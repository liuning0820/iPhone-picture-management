# 按扩展名跳过文件的能力调查

## 摘要结论

当前 `iphone_copy.py` **不支持按文件扩展名跳过文件**，也没有专门跳过
`.MOV` 的命令行参数。只要 `.MOV` 位于匹配的 `YYYYMM_*` 文件夹中，它就会
被加入源文件清单、计入总数和总大小，并进入复制流程。因此，当前没有可用的
命令可以直接表达“复制照片但跳过所有 `.MOV`”。

代码中的“跳过”是另一种含义：如果目标端已经存在同一路径、且文件大小与源文件
一致，复制阶段会跳过该文件；这个逻辑不按扩展名筛选，`.MOV` 同样适用。

本次检查未修改源代码、README、测试或配置，也未重启任何服务。

## 证据

### `iphone_copy.py` 的文件选择流程

- `collect_source_selection()` 遍历匹配的目录及其所有嵌套目录。对每个非目录项，
  它直接创建 `SourceFile` 并追加到 `files`；没有检查文件名后缀、扩展名白名单或
  排除列表。见 `iphone_copy.py:collect_source_selection`，尤其是递归函数
  `walk()` 中处理非目录项的分支。
- `Period.parse()` 和 `Period.matches()` 只筛选顶层的年月目录名称，例如
  `202610_a`；它们不筛选文件类型。见 `iphone_copy.py:Period.parse` 和
  `iphone_copy.py:Period.matches`。
- `copy_selection()` 对 `selection.files` 中的每个文件逐一处理，没有扩展名
  判断。见 `iphone_copy.py:copy_selection` 的 `for index, source in
  enumerate(selection.files, start=1)` 循环。
- `copy_selection()` 中唯一的 `skipped` 分支要求目标路径已经存在、是文件，且
  `target.stat().st_size == source.size`，随后报告
  `跳过（已存在且大小一致）`。见 `iphone_copy.py:copy_selection` 中的
  `if target.exists()` 分支。这个条件按路径和字节数工作，不是“跳过某种扩展名”。
- `build_parser()` 注册的参数只有位置参数 `period`、`--destination`、
  `--device-name`、`--storage-name`、`--overwrite`、`--timeout` 和
  `--list-only`。没有 `--exclude-extension`、`--skip-extension`、
  `--include-extension` 或类似参数。见 `iphone_copy.py:build_parser`。
- `run()` 先调用 `collect_source_selection()`，再把完整的 `selection` 传给
  `copy_selection()` 和 `verify_selection()`。因此，要真正跳过 `.MOV`，必须在
  建立清单时排除它，或在复制前过滤并同步调整校验清单；当前两处都没有这种处理。

### `.MOV` 在现有测试中的行为

- `tests/test_iphone_copy.py:SelectionTests.test_collects_sorted_matching_folders_and_nested_files`
  构造了一个 `IMG_3.MOV`，但选择 `202610` 时它位于 `202611_a`，因此测试只
  证明目录月份筛选，不证明扩展名排除。
- `tests/test_iphone_copy.py:CopyAndVerificationTests._selection` 构造了
  `IMG_2.MOV`，并由
  `test_copies_files_then_verifies_paths_count_and_size` 明确验证它会被复制：
  `backend.copy_calls` 预期包含 `IMG_2.MOV`，并且源、目标计数和大小都包括该文件。
- `tests/test_iphone_copy.py:BlockingCopyTimeoutTests` 也把 `IMG.MOV` 作为复制源
  传给 `WindowsMtpBackend.copy_file()`，说明 `.MOV` 没有被底层复制接口特殊排除。

### README 的命令行说明

- `README.md` 的 **Usage** 部分说明默认目标目录是
  `%USERPROFILE%\Downloads`，也给出了指定 OneDrive 的 PowerShell 命令：

  ```powershell
  python iphone_copy.py 202610 --destination "$env:USERPROFILE\OneDrive"
  ```

- README 说明 `YYYYMM` 选择一个月份，`YYYY` 选择全年匹配月份；这些选择针对
  `YYYYMM_*` 文件夹，不是文件扩展名。
- README 的 **Features** 只承诺复制嵌套照片和视频、跳过目标中大小一致的已有
  文件、处理 `--overwrite`，没有声明按扩展名排除。
- README 的 **Usage** 只推荐 `--list-only` 查看匹配目录、文件数和总大小；该参数
  仍会把 `.MOV` 纳入统计，只是不复制，不能实现“复制 JPG、跳过 MOV”。
- README 的 **Existing files** 部分说明现有文件跳过规则，且该规则是相同字节数，
  与扩展名无关。
- `README.md` 的 **Tests** 指定实际测试命令为：

  ```powershell
  python -m unittest discover -s tests -v
  ```

### 已执行的只读验证

在仓库根目录执行了 README 指定的测试命令，结果为 `Ran 16 tests`、`OK`。
检查 `requirements.txt` 可见唯一固定依赖为 `pywin32==311`；仓库没有发现
`AGENTS.md`、`CONTRIBUTING.md`、`.kiro/steering` 文件，也没有发现
`pyproject.toml` 或 `pytest.ini`。

## 当前可行的命令示例

下面的命令都能按当前实现运行，但都**不能按扩展名跳过 `.MOV`**：

```powershell
# 只查看匹配目录、文件数和总大小；仍会统计 .MOV，不进行复制
python iphone_copy.py 202607 --list-only

# 复制全部匹配文件，包括 .MOV
python iphone_copy.py 202607 --destination "$env:USERPROFILE\OneDrive"

# 复制全部匹配文件，并在单个文件等待时间较长时增加超时
python iphone_copy.py 202607 --destination "$env:USERPROFILE\OneDrive" --timeout 1800
```

不能把类似下面的参数用于当前版本，因为参数解析器没有注册它，会返回
`unrecognized arguments`：

```powershell
python iphone_copy.py 202607 --skip-extension .MOV
```

如果目标目录中已经存在某个 `.MOV` 且路径和大小都与源文件一致，重新运行时它会
被现有文件逻辑跳过；这只能逐个依赖目标文件状态，不能保证跳过所有 `.MOV`，也
不能避免首次读取和统计源文件时访问这些视频。

## 结论与建议

当前真实支持情况是：**不支持按扩展名排除；`.MOV` 默认会被复制；仅支持按目标
文件是否已存在且大小一致来跳过。** 这也解释了示例中程序在处理
`CTVE0260.MOV` 时进入 Windows MTP 等待：该文件已经进入完整源清单，程序没有
任何 `.MOV` 排除分支。

如果要解决该需求，推荐新增一个明确的 CLI 选项，例如
`--exclude-extension .MOV`，并在 `collect_source_selection()` 建立清单时按
大小写不敏感的后缀过滤。过滤必须发生在 `SourceFile` 创建之前，这样文件不会被
计入源文件数量、总大小、复制进度或最终校验；同时应在
`tests/test_iphone_copy.py` 增加至少以下测试：

1. `.MOV`、`.mov` 都被排除，其他扩展名仍被收集；
2. 排除后的文件不会出现在 `copy_calls`，也不会被目标校验当成缺少文件；
3. `--help` 显示该参数，且传入带或不带前导点的扩展名时行为明确；
4. 没有排除参数时保持当前兼容行为，即 `.MOV` 仍会被复制。

README 也应补充参数格式、大小写规则、可重复指定多个扩展名的规则，以及一个
PowerShell 示例。实现前应明确选择一种接口；建议使用可重复的参数形式，例如：

```powershell
python iphone_copy.py 202607 `
  --destination "$env:USERPROFILE\OneDrive" `
  --exclude-extension .MOV
```

上面的命令是**建议实现后的目标示例，不适用于当前版本**。在实现该功能前，
不要把它当作当前可行命令使用。
