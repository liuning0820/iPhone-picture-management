# Skip-extension verification

在仓库根目录 `C:\Users\toby0\github\iPhone-picture-management` 执行了以下命令：

```powershell
python -m unittest discover -s tests -v
```

结果：通过。共运行 21 个测试，全部 `ok`，最终输出为 `OK`。

```powershell
python -m py_compile iphone_copy.py tests/test_iphone_copy.py
```

结果：通过，命令返回码为 0，无语法或编译错误。

验证覆盖：

- `--skip-extension` 单次和重复参数解析。
- `MOV`、`.MOV` 和混合大小写扩展名规范化。
- 扩展名大小写不敏感匹配。
- 文件名主体包含 `MOV` 但后缀不同的文件不会被过滤。
- 被过滤的 MOV 文件不进入源清单，也不会调用 MTP `copy_file()`。
- 未启用过滤时的既有清单和复制行为。
- 目标文件已存在且大小一致时的原有跳过逻辑。
