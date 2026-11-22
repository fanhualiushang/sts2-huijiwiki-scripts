# 换机与复现说明

代码只维护在 `fanhualiushang/sts2-huijiwiki-scripts` 的 `master` 分支。正式导出入口是 `python -m local_game_data export`，它依次运行仓库内的提取器和分类脚本；数据修正规则都保存在 Python 文件中。

## 从 GitHub 恢复代码

在新机器安装 Git 和 Python 3.12，然后运行：

```powershell
git clone https://github.com/fanhualiushang/sts2-huijiwiki-scripts.git
Set-Location sts2-huijiwiki-scripts
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-reproducible.txt
```

`requirements-reproducible.txt` 记录本次验收环境的全部依赖版本，包含 pytest。本次使用 Windows x64、Python 3.12.14、pypinyin 0.55.0，没有安装可选 PyICU；ICU 与 pypinyin 的中文排序不保证完全相同。后续主动更新依赖时，也应重新检查输出。

## 恢复本地维护数据

以下数据被 Git 忽略，**仅克隆 GitHub 仓库不会获得它们**：

| 本地内容 | 恢复方式与用途 |
| --- | --- |
| `local_data/baseline/` | 从原机器复制四份正式 Wiki xlsx，或重新从 Wiki 下载。保存页面名、前作对应、测试画风链接、状态展示约定等人工数据。 |
| `local_data/tabx/history/cards.json`、`relics.json` | 从原机器复制测试版历史归档，保留今后从游戏中删除的卡牌和遗物。 |
| `local_data/0.111.1/tabx/history/cards.json`、`relics.json` | 从原机器复制正式版历史归档，与测试版分开。 |
| 已验收的 `local_data/tabx/*.json`、`local_data/0.111.1/tabx/*.json` | 可一并复制，便于与重新生成的文件对照。 |
| 两个 GDRE 完整恢复目录 | 复制原目录，或使用相同版本的游戏包重新完整恢复。需要 `src/Core/Models` 和 `localization/zhs`；源码、资源和游戏 DLL 不在代码仓库中。 |
| 个人配置和 Wiki 凭据 | 使用下方示例重建配置，按 README 设置凭据环境变量。无需复制旧虚拟环境。 |

本次交接另准备了本地 `maintenance_migration_2026-11-23.zip`：包含 Wiki 基线、两版的卡牌/遗物历史归档、已验收的七类 JSON、便携配置和文件 SHA256 清单。解压到新克隆仓库根目录即可恢复这些数据。它不含反编译源码、游戏资源、凭据或虚拟环境，也没有上传到 GitHub；换机前需要另行带走。

不要只依靠重新下载 Wiki 基线恢复历史：已经不在游戏和 Wiki 基线中的条目，需要原有 `history` 归档才能继续保留。输出目录与正式/测试通道继续分开使用。

## 配置与运行

如果没有恢复迁移包中的配置，先在仓库根目录执行：

```powershell
Copy-Item config.example.json config.json
Copy-Item config.stable.example.json config.stable.json
```

默认布局如下，也可以修改配置里的路径：

```text
任意工作目录/
  sts2-huijiwiki-scripts/
    config.json                  # 测试版 0.113.1
    config.stable.json           # 正式版 0.111.1
    local_data/
      baseline/
      tabx/history/
      0.111.1/tabx/history/
  sts2_source_code/              # 测试版完整恢复目录
  sts2_source_code_0.111.1/       # 正式版完整恢复目录
```

配置里的相对路径以该配置文件所在目录为准。迁移包和示例都使用上述相对布局，避免沿用原机器的盘符。旧机器放在 `local_data/0.111.1/config.json` 的独立配置仍受支持，但它包含本机绝对路径，换机时建议使用根目录的新示例。

在仓库根目录运行：

```powershell
# 测试版：默认读取 config.json
.\.venv\Scripts\python.exe -m local_game_data export

# 正式版：读取独立配置与输出目录
.\.venv\Scripts\python.exe -m local_game_data export --config .\config.stable.json

.\.venv\Scripts\python.exe -m pytest -q
```

正常导出始终离线，不需要 Wiki 凭据。环境中的 `STS2_*` 覆盖项可能优先于配置，若结果指向意外路径或版本，请按 README 检查这些变量。

## 本次验收状态

| 版本 | 卡牌行数 | 遗物行数 | 状态行数 | 怪物行数 |
| --- | ---: | ---: | ---: | ---: |
| 0.113.1 测试版 | 1419 | 322 | 310 | 124 |
| 0.111.1 正式版 | 1179 | 304 | 270 | 122 |

卡牌行数包含升级版，遗物行数包含角色玻璃变体和保留条目。达弗的 12 件遗物已正确归属；五种临时状态统一补齐 `X点`；卡牌和遗物最终排序后再连续编号。本次对两版各重复导出两次，四张主表文件的 SHA256 完全一致。

这些数量用于核对本次源码和基线；后续版本新增或历史数据增加时可以变化。每次导出仍需查看 `report.json`、各表 `.report.json` 和 `baseline_comparison.json` 中的解析缺口与回退记录。真实源码或本地基线缺失时，部分集成测试会跳过，测试通过不能代替恢复这些输入。
