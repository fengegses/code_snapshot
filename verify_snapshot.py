#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_snapshot.py —— code_snapshot.py 的独立验证脚本

用与工具不同的实现路径（os.walk 递归）交叉核对：
1. JSON 树结构与磁盘实际结构完全一致（含中文名、无后缀文件）
2. 符号链接记录为 kind=link 且 target 正确；目录链接不被跟随
3. 压缩包只记录文件名（kind=file），内部文件不出现
4. stats 计数与磁盘统计一致
5. 两次扫描输出确定（tree 部分一致，可 diff）
6. 输出文件放到根目录内时被自动排除（不扫到自己）

用法：python3 verify_snapshot.py
"""

import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "demo_project")
JSON_OUT = os.path.join(BASE, "demo_project_snapshot.json")
FAIL = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def disk_reference():
    """独立遍历磁盘（递归函数实现，故意与工具的实现路径不同）。"""
    dirs, files, links, total = 0, 0, 0, 0
    rel_paths = set()

    def walk(path, rel):
        nonlocal dirs, files, links, total
        for name in sorted(os.listdir(path)):
            p = os.path.join(path, name)
            r = os.path.join(rel, name) if rel else name
            if os.path.islink(p):
                links += 1
                rel_paths.add(("link", r))
            elif os.path.isdir(p):
                dirs += 1
                rel_paths.add(("dir", r))
                walk(p, r)
            else:
                files += 1
                total += os.path.getsize(p)
                rel_paths.add(("file", r))

    walk(ROOT, "")
    return dirs, files, links, total, rel_paths


def tree_walk(node):
    """把 JSON 树拍平成 (kind, rel_path) 集合；rel 相对 ROOT、不含根节点自身。"""
    out = set()

    def walk(n, rel):
        for child in n.get("children", []):
            r = os.path.join(rel, child["name"]) if rel else child["name"]
            out.add((child["kind"], r))
            walk(child, r)

    walk(node, "")
    return out


def find_node(tree, rel_path_parts):
    """按相对路径（parts 列表）在树中查找节点。"""
    node = tree
    for part in rel_path_parts:
        node = next(c for c in node.get("children", []) if c["name"] == part)
    return node


def main():
    if not os.path.exists(JSON_OUT):
        sys.exit("未找到输出文件，请先运行: python3 code_snapshot.py demo_project")

    with open(JSON_OUT, encoding="utf-8") as f:
        doc = json.load(f)

    # 1. 结构一致性：JSON 树 vs 磁盘独立遍历
    check("根节点名 = 根目录名 且为 dir",
          doc["tree"]["kind"] == "dir" and doc["tree"]["name"] == os.path.basename(ROOT),
          f'{doc["tree"].get("name")} / {doc["tree"].get("kind")}')
    d_dirs, d_files, d_links, d_total, d_set = disk_reference()
    t_set = tree_walk(doc["tree"])
    check("JSON 树与磁盘结构完全一致", t_set == d_set,
          f"树 {len(t_set)} 项 vs 磁盘 {len(d_set)} 项，差异: {d_set ^ t_set}")

    # 2. 统计一致
    st = doc["stats"]
    check("stats.dirs 一致", st["dirs"] == d_dirs, f"{st['dirs']} vs {d_dirs}")
    check("stats.files 一致", st["files"] == d_files, f"{st['files']} vs {d_files}")
    check("stats.links 一致", st["links"] == d_links, f"{st['links']} vs {d_links}")
    check("stats.total_bytes 一致", st["total_bytes"] == d_total,
          f"{st['total_bytes']} vs {d_total}")

    # 3. 链接记录
    l1 = find_node(doc["tree"], ["src", "readme_link"])
    check("readme_link 记为 kind=link", l1["kind"] == "link", str(l1))
    check("readme_link target 正确", l1["target"] == "../README.md", l1.get("target"))
    l2 = find_node(doc["tree"], ["src", "broken_link"])
    check("broken_link（悬空链接）不崩溃并记为 link", l2["kind"] == "link", str(l2))

    # 4. 目录符号链接不跟随（无 src_link 目录节点、无循环）
    src = find_node(doc["tree"], ["src"])
    names = [c["name"] for c in src["children"]]
    check("src 下无跟随产生的重复目录", len(names) == len(set(names)), str(names))

    # 5. 压缩包只记名、不打开
    z = find_node(doc["tree"], ["src", "vendor.zip"])
    check("vendor.zip 记为普通文件", z["kind"] == "file", str(z))
    all_text = json.dumps(doc, ensure_ascii=False)
    check("压缩包内部文件未泄漏", "hidden_inside.txt" not in all_text)

    # 6. 中文文件名正常保存（非 \uXXXX 转义）
    check("中文文件名原样保存", "数据文件.txt" in all_text)

    # 7. 确定性：两次扫描 tree 完全一致
    subprocess.run([sys.executable, os.path.join(BASE, "code_snapshot.py"), ROOT],
                   cwd=BASE, check=True, capture_output=True)
    with open(JSON_OUT, encoding="utf-8") as f:
        doc2 = json.load(f)
    check("两次扫描 tree 一致（确定性输出）", doc["tree"] == doc2["tree"])

    # 8. 自排除：输出文件放根目录内
    inside = os.path.join(ROOT, "self_test.json")
    subprocess.run([sys.executable, os.path.join(BASE, "code_snapshot.py"), ROOT,
                    "-o", inside, "--format", "json"],
                   cwd=BASE, check=True, capture_output=True)
    with open(inside, encoding="utf-8") as f:
        doc3 = json.load(f)
    all3 = json.dumps(doc3, ensure_ascii=False)
    check("输出文件自身被排除（放根目录内也不自扫）", "self_test.json" not in all3)
    # 无论成败都先清理自排除测试产物，避免污染磁盘对照
    if os.path.exists(inside):
        os.remove(inside)

    print()
    print("=== 第二阶段：--no-expand 不展开输出 ===")

    def run_no_expand(specs, stdin="\n"):
        return subprocess.run(
            [sys.executable, os.path.join(BASE, "code_snapshot.py"), ROOT,
             "--no-expand", *specs],
            cwd=BASE, input=stdin, text=True, capture_output=True)

    def load_snapshot():
        with open(JSON_OUT, encoding="utf-8") as f:
            return json.load(f)

    def has_node_with(doc, parts, key, value):
        try:
            return find_node(doc["tree"], parts).get(key) == value
        except StopIteration:
            return False

    # 9. 唯一名称：不展开且不交互（stdin 只有回车也不会触发选择）
    r9 = run_no_expand(["utils"], stdin="\n")
    check("唯一名称运行成功", r9.returncode == 0)
    d9 = load_snapshot()
    check("唯一名称目录 expanded=false", has_node_with(d9, ["src", "utils"], "expanded", False))
    t9 = json.dumps(d9, ensure_ascii=False)
    check("不展开目录内容未进入树", "helper.c" not in t9)
    check("stats.no_expand_folders=1", d9["stats"]["no_expand_folders"] == 1,
          str(d9["stats"]["no_expand_folders"]))

    # 10. 重名目录：交互全选（all）
    r10 = run_no_expand(["vendor"], stdin="all\n")
    check("重名全选运行成功", r10.returncode == 0)
    d10 = load_snapshot()
    check("src/vendor expanded=false（全选）",
          has_node_with(d10, ["src", "vendor"], "expanded", False))
    check("third_party/vendor expanded=false（全选）",
          has_node_with(d10, ["third_party", "vendor"], "expanded", False))
    t10 = json.dumps(d10, ensure_ascii=False)
    check("两个 vendor 内容均未进入树", "lib.js" not in t10 and "old.js" not in t10)
    check("stats.no_expand_folders=2（全选）", d10["stats"]["no_expand_folders"] == 2,
          str(d10["stats"]["no_expand_folders"]))

    # 11. 重名目录：只选编号 2（另一个保持展开）
    r11 = run_no_expand(["vendor"], stdin="2\n")
    check("重名单选运行成功", r11.returncode == 0)
    d11 = load_snapshot()
    check("src/vendor 仍展开（未选）", has_node_with(d11, ["src", "vendor"], "expanded", None))
    check("third_party/vendor expanded=false（选中）",
          has_node_with(d11, ["third_party", "vendor"], "expanded", False))
    t11 = json.dumps(d11, ensure_ascii=False)
    check("选中的不展开，未选中的正常展开", "lib.js" in t11 and "old.js" not in t11)
    check("stats.no_expand_folders=1（单选）", d11["stats"]["no_expand_folders"] == 1,
          str(d11["stats"]["no_expand_folders"]))

    # 12. 相对处理根目录的路径：不展开且不交互
    r12 = run_no_expand(["node_modules/pkg"], stdin="\n")
    check("相对路径运行成功", r12.returncode == 0)
    d12 = load_snapshot()
    check("node_modules/pkg expanded=false（路径写法）",
          has_node_with(d12, ["node_modules", "pkg"], "expanded", False))
    t12 = json.dumps(d12, ensure_ascii=False)
    check("pkg 内容未进入树", "index.js" not in t12)

    # 13. 越出根目录的路径：警告并忽略，仍正常生成
    r13 = run_no_expand(["../outside"], stdin="\n")
    check("越界路径不报错（警告+忽略）", r13.returncode == 0,
          f"exit={r13.returncode}, stderr={r13.stderr[:200]}")
    check("越界路径给出警告", "越出了处理根目录范围" in r13.stdout)

    # 14. 系统绝对路径：警告并忽略
    r14 = run_no_expand(["/tmp"], stdin="\n")
    check("绝对路径不报错（警告+忽略）", r14.returncode == 0)
    check("绝对路径给出警告", "系统绝对路径" in r14.stdout)

    # 15. 无效编号：重试后接受合法输入
    r15 = run_no_expand(["vendor"], stdin="9\n2\n")
    check("无效编号后重试成功", r15.returncode == 0 and "超出范围" in r15.stdout)
    d15 = load_snapshot()
    check("重试后按合法编号生效", d15["stats"]["no_expand_folders"] == 1,
          str(d15["stats"]["no_expand_folders"]))

    print()
    if FAIL:
        print(f"验证失败 {len(FAIL)} 项: {FAIL}")
        sys.exit(1)
    print("全部验证通过 ✅")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
