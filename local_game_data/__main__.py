import argparse
from collections import Counter
from pathlib import Path

from .dataset import extract
from .config import load_config


def main():
    parser = argparse.ArgumentParser(description="从 GDRE 恢复目录生成离线游戏数据")
    parser.add_argument("command", choices=("extract", "export", "snapshot-wiki"))
    parser.add_argument("--config", type=Path, help="配置文件；也可设置 STS2_CONFIG")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--version", help="本次解包对应的游戏版本")
    parser.add_argument("--lang")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path, help="已保存的 Wiki tabx 快照目录")
    args = parser.parse_args()
    settings = load_config(args.config)
    source = args.source.resolve() if args.source else settings.source_dir
    output = args.output.resolve() if args.output else settings.output_dir
    version = args.version or settings.version
    language = args.lang or settings.language
    baseline = args.baseline.resolve() if args.baseline else settings.baseline_dir
    dataset_file = output / "dataset.json" if args.output else settings.dataset_file
    if args.command == "snapshot-wiki":
        from .wiki import snapshot_wiki
        # snapshot_wiki and its client use the same selected config as the CLI.
        import os
        previous = os.environ.get("STS2_CONFIG")
        try:
            if args.config: os.environ["STS2_CONFIG"] = str(settings.path)
            snapshot_wiki(baseline if not args.output or args.baseline else output / "baseline")
        finally:
            if previous is None: os.environ.pop("STS2_CONFIG", None)
            else: os.environ["STS2_CONFIG"] = previous
        return
    if not version:
        parser.error("请在 config.json 的 game.version 或 --version 中指定本次游戏版本")
    document = extract(source, version, output, language, dataset_file=dataset_file)
    print("分类数量：", {k: len(v) for k, v in document["routes"].items()})
    print("待核对项：", dict(Counter(i["category"] for i in document["issues"])))
    print("数据与报告：", output)
    if args.command == "export":
        from .wiki import export_all
        export_all(dataset_file, output / "tabx", baseline, settings.path if settings.path.is_file() else None)


if __name__ == "__main__":
    main()
