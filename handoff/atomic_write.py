#!/usr/bin/env python3
"""handoff 原子写入（只用标准库）。

先写同目录下的隐藏临时文件（以 . 开头、.tmp 结尾），fsync 后 os.replace 到目标名。
读方只会看到完整文件。
"""
import json
import os
import tempfile


def write_json_atomic(root, rel_path, obj):
    """把 obj 以 JSON 形式原子写入 root/rel_path，返回目标绝对路径。"""
    target = os.path.join(root, rel_path)
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(target) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return target


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        print("用法: python3 handoff/atomic_write.py <handoff目录> <相对路径> <JSON字符串>")
        sys.exit(2)
    print(write_json_atomic(sys.argv[1], sys.argv[2], json.loads(sys.argv[3])))
