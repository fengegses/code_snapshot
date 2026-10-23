# code_snapshot —— 工程代码结构快照工具

把指定根目录的**完整目录层级 + 文件名（含后缀）+ 基础元数据**保存为结构化文件，
供结构归档、目录重建、版本对比与代码库审计使用。Python 3 标准库实现，零第三方依赖。

## 特性

- **指定根目录，根目录名作项目名**，递归遍历整棵目录树
- **不分文件类型，全量记录**（`.git`、`node_modules`、`__pycache__` 等一律记录）
- **压缩包只记录文件名，不打开内部**（只 `stat`、不 `read`：`.zip/.tar/.rar/.7z` 均如此）
- **文件名含后缀完整保存**（`main.py`、`CMakeLists.txt`、无后缀文件原样记录）
- **`--no-expand` 指定文件夹不展开输出**（原样输出文件夹本身，不深入遍历）：
  - 两种写法：① **文件夹名** —— 递归查找，**重名时列出各路径和编号，等待交互选择**（可多选、可输入 `all` 全选、回车不选）；② **相对「处理根目录」的路径**（如 `src/utils`）
  - 只接受相对处理根目录的路径：**系统绝对路径、越出根目录的路径一律拒绝**（根目录外结构不处理）
- **生成后自动验证**：每次生成完毕立即校验——JSON 回读一致 / 节点结构合法 / 统计与树一致 / 输出文件自排除 / 独立遍历磁盘交叉核对 / MD 文件头完整；失败则列出明细并以非零退出（可用 `--no-verify` 跳过）
- 每层目录按名称排序 → 输出确定，两次扫描可 `diff`
- **符号链接不跟随**（防循环），记录 `kind=link` 与目标路径；悬空链接不崩溃
- **权限错误不中断**，记入 `stats.skipped` 与 `skipped` 列表
- **自动排除输出文件自身**（输出放到根目录内也不会把自己扫进去）
- 迭代遍历（显式栈），深目录不爆栈

## 用法

```bash
python3 code_snapshot.py <root_dir> [-o 路径] [--format json|md|both] [--no-expand 名称|相对路径 ...]
```

| 参数 | 说明 |
|---|---|
| `root_dir` | 要扫描的项目根目录（必填） |
| `-o 路径` | 输出路径；`both` 模式下作基础名，自动补 `.json`/`.md`；默认输出到**根目录外**（根目录同级） |
| `--format` | `json` / `md` / `both`（默认 `both`） |
| `--no-expand` | 不展开输出的文件夹，一次可多个、可重复指定。**文件夹名**（重名时交互选择）或**相对处理根目录的路径**（如 `src/utils`）；绝对路径与越出根目录的路径被拒绝（警告并忽略） |
| `--no-verify` | 生成后跳过自动验证（默认每次生成后自动验证） |

示例：

```bash
python3 code_snapshot.py ./demo_project              # 输出 demo_project_snapshot.json + .md
python3 code_snapshot.py ./demo_project --format md  # 只要 Markdown 树
python3 code_snapshot.py ./demo_project -o /tmp/x --format json
python3 code_snapshot.py ./demo_project --no-expand vendor          # 名称；重名时交互选择编号
python3 code_snapshot.py ./demo_project --no-expand src/utils       # 相对处理根目录的路径
python3 code_snapshot.py ./demo_project --no-expand utils vendor node_modules/pkg
```

重名交互示例（`--no-expand vendor`，根目录下有两个 `vendor`）：

```text
发现重名目录 “vendor”，共 2 个（可多选）：
  1) /path/demo_project/src/vendor
  2) /path/demo_project/third_party/vendor
请输入编号（逗号/空格分隔；all=全选；直接回车=不选择）：all
```

## 输出格式

### JSON（`<项目名>_snapshot.json`）

```json
{
  "schema_version": 2,
  "project": "myapp",
  "root": "/abs/path/myapp",
  "generated_at": "2026-10-22T10:00:00+08:00",
  "stats": { "dirs": 12, "files": 86, "links": 2, "no_expand_folders": 1,
             "total_bytes": 1543210, "skipped": 0, "elapsed_sec": 0.03 },
  "skipped": [],
  "tree": { "name": "myapp", "kind": "dir", "children": [
    { "name": "vendor", "kind": "dir", "expanded": false, "mtime": "..." }
  ] }
}
```

节点字段：`name`（含后缀）/ `kind`（`file`|`dir`|`link`）/ `size` / `mtime`（本地时区 ISO 8601）/
`target`（仅 link）/ `children`（仅展开的 dir）/ `expanded: false`（仅**不展开**的 dir，缺省视为已展开）。
中文文件名原样保存（`ensure_ascii=False`）。

### Markdown（`<项目名>_snapshot.md`）

`│ ├── └──` 树形图，目录以 `/` 结尾；**不展开的目录追加 `（未展开）` 标记**，便于直接阅读和贴进文档。

## 文件清单

| 文件 | 作用 |
|---|---|
| `code_snapshot.py` | 工具本体（单文件） |
| `verify_snapshot.py` | 独立验证脚本：结构一致性 / 链接 / 压缩包 / 统计 / 确定性 / 自排除 / no-expand 交互与边界 |
| `demo_project/` | 测试样例项目（含重名目录、中文名、压缩包、悬空链接、深目录、无后缀文件等边界） |
| `demo_project_snapshot.json` / `.md` | 样例运行产物（含 4 个不展开目录） |

## 验证

**生成后自动验证**（内置，默认开启）：

```text
验证       : 6/6 通过（自动）
```

每次运行 `code_snapshot.py` 生成完毕即自动校验，失败会逐项打印明细并以非零退出；`--no-verify` 可跳过。

开发期回归套件（含交互、警告、边界等 29 项独立检查）：

```bash
python3 verify_snapshot.py   # 29 项检查全过
```

## 已知限制

- 不保存文件内容（设计预留 `--with-content`，后续按需实现）
- `--no-expand` 交互选择需要终端输入；在非交互环境（stdin 不可读）下遇到重名会中止并退出
- 文件名含换行等控制字符时，Markdown 树中以 `\n` 转义显示；JSON 原样保存
- 非法 UTF-8 文件名：JSON 写失败时自动回退 ASCII 转义（极少见边界）
