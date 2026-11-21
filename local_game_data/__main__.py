import argparse
from collections import Counter
from pathlib import Path

from .dataset import extract


def main():
    parser = argparse.ArgumentParser(description="从 GDRE 恢复目录生成离线游戏数据")
    parser.add_argument("command", choices=("extract", "export", "snapshot-wiki"))
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2] / "sts2_source_code")
    parser.add_argument("--version", help="本次解包对应的游戏版本")
    parser.add_argument("--lang", default="zhs")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "local_data")
    parser.add_argument("--baseline", type=Path, help="已保存的 Wiki tabx 快照目录")
    args = parser.parse_args()
    if args.command == "snapshot-wiki":
        from .wiki import snapshot_wiki
        snapshot_wiki(args.output / "baseline")
        return
    if not args.version:
        parser.error("extract/export 需要 --version，避免把旧数据标成新版本")
    document = extract(args.source, args.version, args.output, args.lang)
    print("分类数量：", {k: len(v) for k, v in document["routes"].items()})
    print("待核对项：", dict(Counter(i["category"] for i in document["issues"])))
    print("数据与报告：", args.output.resolve())
    if args.command == "export":
        from .wiki import export_all
        export_all(args.output / "dataset.json", args.output / "tabx", args.baseline)


if __name__ == "__main__":
    main()
