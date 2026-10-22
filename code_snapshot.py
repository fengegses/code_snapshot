#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code_snapshot.py —— 工程代码结构快照工具

将指定根目录的完整目录层级、文件名（含后缀）与基础元数据
保存为结构化文件（JSON + 可选 Markdown 树形图），供结构归档、
目录重建、版本对比与代码库审计使用。

特性
- 指定根目录，取根目录名作为项目名，递归遍历整棵目录树
- 不分文件类型，全量记录；压缩包（.zip/.tar/.rar/.7z 等）只记录
  文件名，绝不打开内部（只 stat、不 read）
- 文件名含后缀完整保存；每层目录按名称排序，输出确定、可 diff
- 符号链接不跟随（防循环），记录 kind=link 与目标路径
- 权限错误不中断，记入 stats.skipped 与 skipped 列表
- 自动排除输出文件自身（输出放到根目录内时也不会把自己扫进去）
- --no-expand：指定一个/多个文件夹「不展开」输出（原样输出文件夹本身）
  · 两种写法：① 文件夹名（重名时列出路径与编号交互选择，可多选/全选）
              ② 相对「处理根目录」的路径（如 src/utils；系统绝对路径与
                 越出根目录的路径一律拒绝——根目录外结构不处理）
- 默认同时输出 <项目名>_snapshot.json 与 <项目名>_snapshot.md

用法
  python code_snapshot.py <root_dir> [-o 路径] [--format json|md|both] [--no-expand 名称|相对路径 ...]

示例
  python code_snapshot.py ./demo_project
  python code_snapshot.py ./demo_project --format md -o tree.md
  python code_snapshot.py ./demo_project --no-expand vendor          # 名称；重名时交互选择
  python code_snapshot.py ./demo_project --no-expand src/utils       # 相对处理根目录的路径
  python code_snapshot.py ./demo_project --no-expand utils vendor node_modules/pkg
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

SCHEMA_VERSION = 2


def fsname(p):
    """把路径/文件名安全解码为 str（处理非法 UTF-8 文件名，不崩溃）。"""
    return os.fsdecode(p)


def fmt_size(n):
    """字节数转人类可读（B/KB/MB/GB/TB）。"""
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def fmt_time(ts):
    """时间戳 → 本地时区 ISO 8601（含 UTC 偏移）。"""
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def resolve_outputs(root_abs, root_name, output, fmt):
    """计算输出文件路径列表。

    fmt 为 json|md 时输出单文件；为 both 时输出两个文件。
    默认输出到根目录外（根目录同级），名为 <根目录名>_snapshot.{json,md}。
    -o 指定路径时：both 模式按扩展名推导另一个文件，无扩展名则补全。
    """
    base = os.path.join(os.path.dirname(root_abs), root_name + "_snapshot")
    if output:
        out = os.path.abspath(output)
        low = out.lower()
        if fmt == "both":
            if low.endswith(".json"):
                return [out, out[:-5] + ".md"]
            if low.endswith(".md"):
                return [out[:-3] + ".json", out]
            return [out + ".json", out + ".md"]
        if fmt == "json":
            return [out if low.endswith(".json") else out + ".json"]
        return [out if low.endswith(".md") else out + ".md"]
    if fmt == "json":
        return [base + ".json"]
    if fmt == "md":
        return [base + ".md"]
    return [base + ".json", base + ".md"]


def find_dirs_by_name(root_abs, name, out_set):
    """迭代遍历，收集根目录内所有 basename == name 的目录绝对路径（含根自身）。"""
    matches = []
    if fsname(os.path.basename(root_abs)) == name:
        matches.append(root_abs)
    stack = [root_abs]
    while stack:
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: fsname(e.name))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            abs_p = os.path.abspath(entry.path)
            if abs_p in out_set:
                continue
            if fsname(entry.name) == name:
                matches.append(abs_p)
            stack.append(abs_p)
    return sorted(matches)


def prompt_select(name, matches):
    """重名目录交互选择：列出路径与编号，等待用户选择，返回选中绝对路径集合。

    支持：逗号/空格分隔多选、all 全选、直接回车不选。非交互环境（无 stdin）下退出。
    """
    print(f"发现重名目录 “{name}”，共 {len(matches)} 个（可多选）：")
    for i, p in enumerate(matches, 1):
        print(f"  {i}) {p}")
    while True:
        try:
            raw = input("请输入编号（逗号/空格分隔；all=全选；直接回车=不选择）：").strip()
        except EOFError:
            print("\n[中止] 交互选择无输入（非交互环境），已退出。")
            sys.exit(2)
        if not raw:
            return set()
        if raw.lower() == "all":
            return set(matches)
        parts = re.split(r"[,，\s]+", raw)
        try:
            idxs = {int(x) for x in parts if x}
        except ValueError:
            print("  编号无效，请重新输入。")
            continue
        if not idxs or not idxs <= set(range(1, len(matches) + 1)):
            print("  编号超出范围，请重新输入。")
            continue
        return {matches[i - 1] for i in idxs}


def resolve_no_expand(root_abs, specs, output_paths):
    """把 --no-expand 输入解析为具体目录绝对路径集合。

    - 含路径分隔符（或 . / ..）→ 相对「处理根目录」的路径；
      系统绝对路径与越出根目录的路径一律拒绝（根目录外结构不处理）。
    - 否则 → 文件夹名；重名时列出路径和编号交互选择。
    返回 (no_expand_set, warnings)；无法满足的输入记入 warnings，不阻塞生成。
    """
    out_set = set(output_paths)
    no_expand = set()
    warnings = []
    for spec in specs or []:
        spec = spec.strip()
        if not spec:
            continue
        if "/" in spec or "\\" in spec or spec in (".", ".."):
            # —— 相对处理根目录的路径形式 ——
            if os.path.isabs(spec):
                warnings.append(
                    f"--no-expand “{spec}” 是系统绝对路径，请改用相对"
                    f"“{fsname(os.path.basename(root_abs))}”根目录的路径"
                    f"（根目录外结构一律不处理）")
                continue
            norm = os.path.normpath(spec)
            joined = os.path.normpath(os.path.join(root_abs, norm))
            if joined != root_abs and not joined.startswith(root_abs + os.sep):
                warnings.append(
                    f"--no-expand “{spec}” 越出了处理根目录范围，已忽略"
                    f"（根目录外结构不处理）")
                continue
            if not os.path.isdir(joined):
                warnings.append(f"--no-expand “{spec}” 未找到对应目录（{joined}），已忽略")
                continue
            no_expand.add(joined)
        else:
            # —— 文件夹名形式 ——
            matches = find_dirs_by_name(root_abs, spec, out_set)
            if not matches:
                warnings.append(f"--no-expand “{spec}” 在根目录内未找到同名目录，已忽略")
            elif len(matches) == 1:
                no_expand.add(matches[0])
            else:
                no_expand |= prompt_select(spec, matches)
    return no_expand, warnings


def build_tree(root_abs, output_paths, no_expand_set):
    """迭代遍历根目录，构建节点树（显式栈，非递归函数，防深目录爆栈）。

    no_expand_set 中的目录：输出为 {"kind":"dir","expanded":false}，不展开、
    不深入遍历（原样输出文件夹本身）。
    返回 (root_node, stats, skipped)：
    - root_node: {"name", "kind":"dir", "children":[...]}
    - 节点字段：name(含后缀) / kind(file|dir|link) / size / mtime /
      target(仅 link) / expanded(仅不展开的 dir，值为 false) / children(仅展开的 dir)
    - stats: {"dirs","files","links","total_bytes","skipped","no_expand_folders"}
    """
    out_set = set(output_paths)
    stats = {"dirs": 0, "files": 0, "links": 0,
             "total_bytes": 0, "skipped": 0, "no_expand_folders": 0}
    skipped = []

    def new_node(name, kind, **kw):
        node = {"name": name, "kind": kind}
        node.update(kw)
        return node

    root_name = fsname(os.path.basename(root_abs)) or "/"
    root_mtime = fmt_time(os.stat(root_abs).st_mtime)
    if root_abs in no_expand_set:
        # 整个根目录不展开：只输出根节点本身
        root_node = new_node(root_name, "dir", expanded=False, mtime=root_mtime)
        stats["no_expand_folders"] = 1
        return root_node, stats, skipped

    root_node = new_node(root_name, "dir", mtime=root_mtime, children=[])
    stack = [(root_abs, root_node)]

    while stack:
        path, parent = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: fsname(e.name))
        except OSError as err:
            stats["skipped"] += 1
            skipped.append(fsname(err.filename) if err.filename else str(err))
            continue

        children = []
        for entry in entries:
            abs_p = os.path.abspath(entry.path)
            if abs_p in out_set:
                continue  # 排除输出文件自身
            name = fsname(entry.name)
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                stats["skipped"] += 1
                skipped.append(abs_p)
                continue

            if entry.is_dir(follow_symlinks=False):
                if abs_p in no_expand_set:
                    # 不展开：原样输出文件夹本身，不深入遍历
                    child = new_node(name, "dir", expanded=False,
                                     mtime=fmt_time(st.st_mtime))
                    children.append(child)
                    stats["dirs"] += 1
                    stats["no_expand_folders"] += 1
                    continue
                child = new_node(name, "dir",
                                 mtime=fmt_time(st.st_mtime),
                                 children=[])
                children.append(child)
                stats["dirs"] += 1
                stack.append((abs_p, child))
            elif entry.is_symlink():
                node = new_node(name, "link",
                                target=fsname(os.readlink(entry.path)),
                                size=st.st_size,
                                mtime=fmt_time(st.st_mtime))
                children.append(node)
                stats["links"] += 1
            else:
                node = new_node(name, "file",
                                size=st.st_size,
                                mtime=fmt_time(st.st_mtime))
                children.append(node)
                stats["files"] += 1
                stats["total_bytes"] += st.st_size

        parent["children"] = children

    return root_node, stats, skipped


def dump_json(root_node, meta, stats, skipped, path):
    doc = {
        "schema_version": SCHEMA_VERSION,
        "project": meta["project"],
        "root": meta["root"],
        "generated_at": meta["generated_at"],
        "stats": stats,
        "skipped": skipped,
        "tree": root_node,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    except UnicodeEncodeError:
        # 罕见边界：存在无法以 UTF-8 编码的代理字符文件名时，
        # 回退为 ASCII 转义输出，保证文件可写可解析。
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=True, indent=2)


def render_tree_lines(node):
    """把节点树渲染为 │ ├── └── 树形图文本行；不展开目录追加“（未展开）”标记。"""
    lines = []

    def walk(n, prefix, is_last, is_root):
        safe = n["name"].replace("\n", "\\n")
        marker = "  （未展开）" if n.get("expanded") is False else ""
        if is_root:
            lines.append(safe + "/" + marker)
            child_prefix = ""
        else:
            connector = "└── " if is_last else "├── "
            suffix = "/" if n["kind"] == "dir" else ""
            lines.append(prefix + connector + safe + suffix + marker)
            child_prefix = prefix + ("    " if is_last else "│   ")
        children = n.get("children", [])
        for i, child in enumerate(children):
            walk(child, child_prefix, i == len(children) - 1, False)

    walk(node, "", True, True)
    return lines


def dump_md(root_node, meta, stats, skipped, path):
    head = [
        f"# {meta['project']} 目录结构快照",
        "",
        f"- 项目根目录：{meta['root']}",
        f"- 生成时间：{meta['generated_at']}",
        f"- 统计：{stats['dirs']} 个目录 / {stats['files']} 个文件 / "
        f"{stats['links']} 个链接 · 未展开 {stats['no_expand_folders']} 个目录 · "
        f"合计 {fmt_size(stats['total_bytes'])} · 跳过 {stats['skipped']} 项",
        "",
        "## 目录树",
        "",
        "```text",
    ]
    body = render_tree_lines(root_node)
    tail = ["```", ""]
    content = "\n".join(head + body + tail)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="工程代码结构快照工具：把目录层级与文件名（含后缀）保存为结构化文件")
    parser.add_argument("root_dir", help="要扫描的项目根目录")
    parser.add_argument("-o", "--output",
                        help="输出文件路径（both 模式下作为基础名；默认输出到根目录外）")
    parser.add_argument("--format", choices=["json", "md", "both"], default="both",
                        help="输出格式（默认 both：JSON + Markdown）")
    parser.add_argument("--no-expand", nargs="+", action="append",
                        metavar="NAME|REL_PATH",
                        help="不展开输出的文件夹，可多次指定或一次多个。"
                             "两种写法：① 文件夹名（重名时列出路径与编号交互选择，"
                             "可多选/全选）；② 相对“处理根目录”的路径（如 src/utils，"
                             "系统绝对路径与越出根目录的路径会被拒绝）。")
    args = parser.parse_args(argv)

    root_abs = os.path.abspath(args.root_dir)
    if not os.path.isdir(root_abs):
        parser.error(f"根目录不存在或不是目录: {args.root_dir}")

    root_name = fsname(os.path.basename(root_abs)) or "/"
    outputs = resolve_outputs(root_abs, root_name, args.output, args.format)

    specs = []
    if args.no_expand:
        for group in args.no_expand:
            specs.extend(group)
    no_expand, warnings = resolve_no_expand(root_abs, specs, outputs)

    start = time.perf_counter()
    root_node, stats, skipped = build_tree(root_abs, outputs, no_expand)
    elapsed = time.perf_counter() - start
    stats["elapsed_sec"] = round(elapsed, 3)

    meta = {
        "project": root_name,
        "root": root_abs,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    written = []
    try:
        for path in outputs:
            if path.lower().endswith(".md"):
                dump_md(root_node, meta, stats, skipped, path)
            else:
                dump_json(root_node, meta, stats, skipped, path)
            written.append(path)
    except OSError as err:
        parser.error(f"写入输出文件失败: {err}")

    print(f"项目名     : {root_name}")
    print(f"根目录     : {root_abs}")
    print(f"目录/文件  : {stats['dirs']} / {stats['files']}  "
          f"(链接 {stats['links']}，未展开 {stats['no_expand_folders']})")
    print(f"总大小     : {fmt_size(stats['total_bytes'])}")
    print(f"跳过项     : {stats['skipped']}")
    print(f"耗时       : {elapsed:.3f}s")
    for w in warnings:
        print(f"[警告] {w}")
    for p in written:
        print(f"已写出     : {p}")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
