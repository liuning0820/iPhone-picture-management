# MOV MTP 长时间等待调查报告

## 摘要结论

当前实现不是无限等待：每个文件默认有 600 秒超时，可通过
`--timeout` 调整；超时后父进程会终止隔离的 MTP 复制进程并返回错误。用户在等待期间按一次 `Ctrl+C` 通常可以安全取消，主进程会捕获 `KeyboardInterrupt`、清理子进程并返回退出码 130，但 Windows Shell/MTP 可能已经创建了部分目标文件，因此取消后仍必须检查目标文件，不能把“安全取消”理解为“绝不会留下半成品”。

`CTVE0260.MOV` 这个名称、`.MOV` 扩展名和显示出来的相对路径本身没有触发代码拒绝的迹象。更可能的原因是 iPhone/Windows MTP 对该文件的读取或 `Shell.CopyHere` 卡住、原片尚未从 iCloud 下载、iPhone 锁定或 MTP 状态异常；另外本次目标是 OneDrive，OneDrive、杀毒软件或索引器短暂占用目标文件，也可能使代码继续等待，因为完成条件同时要求文件大小正确且文件可独占读取。

立即处理建议：可以按一次 `Ctrl+C` 结束本次运行，等待命令返回“已取消”，然后检查
`C:\Users\<用户>\OneDrive\202607_a\CTVE0260.MOV`。若文件存在但大小不确定或不完整，不要把它当作成功副本；删除该半成品后重试，或在确认可覆盖后使用 `--overwrite`。保持 iPhone 解锁、屏幕唤醒并信任电脑，先在文件资源管理器或 Windows Photos 中单独复制该 MOV；若单独复制也卡住，重新连接数据线并在 iPhone 上打开视频以确保原片已下载。重试时可使用更短的 `--timeout 120`，避免再次等待十分钟。

## 证据

### 1. 超时、取消和失败处理

- `iphone_copy.py:322-394` 的 `WindowsMtpBackend.copy_file` 为每个文件创建
  `multiprocessing` 的 `spawn` 子进程，在 `process.start()` 后按约 0.5 秒轮询。
  计时从启动子进程前后开始，达到传入的 `timeout` 后调用
  `process.terminate()`，最多等待 5 秒；若仍存活，再调用 `process.kill()`，再等待
  5 秒，然后抛出 `IPhoneCopyError`。因此，代码对卡在 `CopyHere` 内的调用有硬超时和强制终止路径。
- `iphone_copy.py:396-441` 的 `_copy_mtp_file_worker` 在子进程内调用
  `destination.CopyHere(source_item, copy_flags)`。该调用之后，worker 每 0.25 秒检查目标文件
  是否达到预期大小并且可独占读取；只有满足条件才把 `("ok", "")` 放入队列。子进程异常会转成
  `("error", detail)`，父进程会报告“复制失败”；没有结果或异常退出码则报告“复制进程异常结束”。
- `iphone_copy.py:387-394` 的 `finally` 在正常返回、超时或父进程异常离开等待循环时，仍会终止存活的
  子进程、等待它退出并关闭结果队列。这是 Ctrl+C 能清理复制 worker 的关键路径。
- `iphone_copy.py:730-750` 的参数默认 `--timeout 600.0`，命令行可覆盖；
  `iphone_copy.py:756-759` 拒绝小于等于零的值。
- `iphone_copy.py:803-814` 的 `main` 捕获 `KeyboardInterrupt`，打印“已取消。”并返回 130。
  `run` 自身有 `finally`，会释放 COM 对象并调用 `backend.close()`。因此一次普通的
  `Ctrl+C` 会结束父进程控制流程并触发资源清理，而不是把 Python 进程永久留在等待循环中。
- `README.md` 的“Troubleshooting”和“Verification”部分也明确记录了默认/可调超时、超时会终止隔离
  复制进程、取消可能留下部分目标文件，以及下次运行需要检查或使用 `--overwrite`。

### 2. 对当前日志的解释

- `iphone_copy.py:353-366` 每 10 秒调用状态回调，文本固定为“仍在等待 Windows MTP”，所以这条日志
  不能区分三种状态：Shell `CopyHere` 尚未返回、目标文件尚未达到预期大小，或者目标文件大小已正确但仍被
  Windows 进程打开。
- `iphone_copy.py:424-430` 表明 `CopyHere` 是异步启动点；worker 随后只等待
  `_file_has_expected_size`。因此日志持续 140 秒并不单独证明 COM 调用仍卡在入口，也可能是传输或目标
  文件句柄尚未稳定。
- 当前命令没有传 `--timeout`，所以实际使用默认 600 秒，而不是 10 秒或 140 秒。达到 600 秒前不会自动
  报错或转到下一个文件。

### 3. 文件名、扩展名、路径和设备端状态

- `iphone_copy.py:450-458` 的 `_validate_windows_component` 禁止空名、`.`、`..`、Windows 保留字符
  `<>:"/\\|?*`，以及以空格或句点结尾的名称。`CTVE0260.MOV` 不违反这些规则。
- `iphone_copy.py:482-505` 将源路径组件按原名保存，并拒绝不区分大小写的 Windows 路径冲突；
  `iphone_copy.py:587-590` 按相对组件建立目标父目录。因此日志中的
  `202607_a\CTVE0260.MOV` 是普通的一层文件路径，没有可见的深层路径或特殊字符风险。
- 源查找在 `iphone_copy.py:419-420` 通过最后一个名称查找，文件扩展名没有单独的分支；
  `.MOV` 不会触发特殊处理。测试中的 `IMG_2.MOV` 也按普通文件成功复制，见
  `tests/test_iphone_copy.py:145-180`。
- 设备端状态仍然可能是根因，但不是由文件名推断出来的：iPhone 必须保持解锁并信任 Windows；
  如果视频原片只在 iCloud，Windows MTP 导入可能等待设备先取得原片；视频本身损坏或 iPhone/MTP
  shell 对该视频读取失败也可能导致 `CopyHere` 不完成。README 的故障排查明确建议打开视频让原片下载，
  并用资源管理器或 Photos 单独验证该文件。
- 目标是 OneDrive，这是本次路径的特殊因素。`iphone_copy.py:427-430` 最终检查目标文件时，
  `_file_has_expected_size`（`iphone_copy.py:528-537`）不仅比较字节数，还调用
  `_windows_file_is_exclusively_readable`（`iphone_copy.py:515-526`）。该检查以“不允许共享”的方式打开文件；
  若 OneDrive 同步、Windows Defender 或索引器仍持有文件句柄，即使传输字节数已经正确，worker 也会继续
  等待并继续显示“仍在等待 Windows MTP”。这使 OneDrive 锁定成为需要实际观察的替代解释。
- 代码没有按扩展名选择不同复制策略，也没有媒体解码或转码逻辑。因此无法仅凭 `.MOV` 判断是兼容性问题；
  应先比较资源管理器/Photos 对同一源文件的行为。

### 4. 测试证据

- `tests/test_iphone_copy.py:357-414` 的
  `BlockingCopyTimeoutTests.test_windows_copy_terminates_a_worker_blocked_inside_copyhere` 用假进程验证
  `copy_file` 超时会调用 `terminate()`，并报告“复制超时”。这直接覆盖了卡在 `CopyHere` 的隔离和超时设计。
- `tests/test_iphone_copy.py:145-180` 使用 `IMG_2.MOV` 验证普通 MOV 文件会进入复制和最终校验流程。
- `tests/test_iphone_copy.py:140-143` 验证大小写不同的 Windows 路径冲突会被拒绝；当前文件名不属于该情况。
- 已按 README 的实际命令运行：

  ```powershell
  python -m unittest discover -s tests -v
  ```

  结果为 `Ran 16 tests ... OK`。本次没有修改代码或测试。只读调查期间的 Git 状态显示工作区原本已有
  `README.md` 修改、`.kiro/settings/lsp.json` 新增，以及未跟踪的 `.agents/`；本报告是用户明确要求创建的
  调查输出，不应覆盖或解释为这些既有变更。

## 结论和不修改代码的立即建议

1. **现在取消：** 在等待时按一次 `Ctrl+C`，不要连续反复按键。等待命令返回“已取消”（退出码 130）。
   如果终端没有及时返回，先再等待清理阶段完成；代码的 `finally` 会尝试终止 worker。如果进程仍无法退出，
   可在确认没有其他重要复制任务后结束该 Python 进程，但这属于最后手段，因为 MTP/OneDrive 可能仍在收尾。
2. **检查目标文件：** 查看 OneDrive 下对应文件的实际字节大小。若文件缺失、大小小于源清单中的
   8.64 MiB，或仍被占用，删除半成品后重试；若目标存在且大小一致，仍建议确认视频能正常播放，并让 OneDrive
   完成同步。不要仅依据文件存在判断复制成功。
3. **先隔离变量：** 暂时把目标改到本地非同步目录，例如
   `C:\Users\<Windows-user>\Downloads\iphone-mtp-test`，再只复制/验证这个月，或至少对该 MOV 做单独的
   资源管理器复制测试。这样可以区分 MTP 源读取问题和 OneDrive 目标锁定/同步问题。
4. **检查设备：** 保持 iPhone 解锁、屏幕唤醒和 USB 连接稳定；在 iPhone 上打开该视频等待原片可用；关闭正在
   操作 iPhone 的资源管理器复制窗口和 Photos 导入任务。若资源管理器/Photos 也无法复制该文件，重新连接数据线、
   重新信任设备，并优先修复设备端可读性。
5. **控制重试等待：** 使用例如
   `python iphone_copy.py 202607 --destination "$env:USERPROFILE\OneDrive" --timeout 120`
   进行诊断性重试。超时值不是修复 MTP 的方法，只是把失败更快显式化；如果源或目标确实需要更长时间，再增加
   数值。
6. **避免错误覆盖：** 若取消后目标已有同名不同大小文件，默认行为会停止并要求确认；先人工检查，再删除半成品或
   在确认来源可靠后使用 `--overwrite`。文件大小一致时，程序会跳过该文件，但最终仍会按路径、数量和大小校验。

## 明显的改进方向（仅建议，不在本次调查中实施）

- 将当前通用的“仍在等待 Windows MTP”拆成更明确的阶段状态，例如区分 worker 尚未返回、目标文件大小增长中、
  目标文件大小已达标但被其他进程占用；这样能直接判断 OneDrive/Defender 锁定而不是误归因于 MTP。
- 为“目标文件达到预期大小但无法独占读取”增加独立的短期目标锁定超时和诊断信息，包括文件当前大小、
  是否存在及已等待时间。当前单一 `--timeout` 覆盖源查找、CopyHere、传输和最终句柄释放，定位粒度不足。
- 对 Ctrl+C 增加显式的用户提示，说明正在终止隔离 worker，并在返回前报告检测到的部分文件；这不会改变取消语义，
  但能降低用户误以为按键无效而重复操作的风险。
- 为测试补充：模拟 worker 已生成正确大小但独占打开失败；模拟 `Ctrl+C` 时验证 worker 被终止并返回 130；
  以及验证 OneDrive/目标锁定不会被错误报告为无限 MTP 调用。当前测试已覆盖 `CopyHere` 超时，但没有覆盖这两个
  真实故障分支。
- 如果后续需要更可靠地识别传输失败，可在 worker 中记录 `CopyHere` 后目标文件出现、大小变化和句柄释放的时间点，
  并在超时错误中带出这些事实。不要仅凭扩展名对 MOV 采用另一套复制逻辑，因为当前证据不支持“`.MOV` 文件名
  本身是根因”。
