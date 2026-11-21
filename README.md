# 杀戮尖塔 2 灰机 Wiki 脚本（本地数据源）

在原有维护脚本基础上，将 Spire Codex API 和模组 `items.json` 替换为 GDRE 完整恢复目录。保留原来的 `utils.data`、`get_data_by_api(route)` 和分类脚本输出格式。代码源自 [HarryS561/sts2-huijiwiki-scripts](https://github.com/HarryS561/sts2-huijiwiki-scripts)，在本 fork 独立维护。

## 安装

需要 Python 3.12 或以上。先创建自己的虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

完成安装后，提取和生成数据不需要网络。原有 PyICU 中文排序可继续使用；未安装 PyICU 时使用 pypinyin 排序。

## 配置

首次将 `config.example.json` 复制为 `config.json`。个人配置已被 Git 忽略，仓库只提交不含凭据的示例文件。

- `game`：恢复目录、游戏版本、语言。每次解包后更新 `game.version`。
- `paths`：输出目录、本地数据文件、Wiki 快照和图片目录。`dataset_file`、`baseline_dir` 留为 `null` 时，自动使用输出目录中的 `dataset.json` 和 `baseline`。
- `wiki`：站点、离线开关、凭据所用的环境变量名，以及各分类对应的 `Data:*.tabx` 页面。

配置中的所有相对路径以配置文件所在目录为基准，不受运行脚本的当前目录影响。优先级为 **命令行参数 > 环境变量 > config.json > 内置默认值**。用 `--config` 或 `STS2_CONFIG` 选择其他配置文件；命令行路径以当前工作目录为基准。

`wiki.username_env`、`password_env`、`authkey_env` 填的是环境变量的名字，默认分别为 `HUIJI_WIKI_USERNAME`、`HUIJI_WIKI_PASSWORD`、`HUIJI_WIKI_STS2_KEY`。账号、密码和 authkey 的实际值通过这些环境变量提供，不写进配置文件；仅在显式连接 Wiki 时读取。`authkey_env` 可设为 `null`，表示不发送该请求头。

旧版 `huijiwiki.username/password/X-authkey` 明文配置需要迁移；加载器会提示迁移，错误信息不包含凭据值。

保留的环境覆盖项为 `STS2_SOURCE`、`STS2_VERSION`、`STS2_LANG`、`STS2_OUTPUT`、`STS2_DATASET`、`STS2_WIKI_BASELINE`、`STS2_CARD_IMAGES`、`STS2_ANIMATIONS`、`STS2_OFFLINE`。`STS2_OFFLINE` 接受 `1/0` 或 `true/false`。正常数据导出始终强制离线；读取 Wiki 快照时需要关闭离线开关。

路径环境变量的相对值也以配置文件目录为基准。临时 `--output` 覆盖本次数据和表格输出位置，Wiki 快照仍按配置读取，用 `--baseline` 单独切换。

## 每个新版本

1. 使用 GDRE 完整恢复游戏资源和 `sts2.dll`，放到相邻的 `sts2_source_code`。其中 `src/Core/Models` 应有实际 C# 内容，不能只有空占位文件。
2. 更新 `config.json` 的 `game.version`，然后运行：

```powershell
.\.venv\Scripts\python.exe -m local_game_data export
```

也可临时覆盖版本和路径：

```powershell
.\.venv\Scripts\python.exe -m local_game_data export --source ..\sts2_source_code --version 0.113.1
```

生成的 `local_data/dataset.json` 是本地数据源，`local_data/report.json` 记录全部提取缺口；原格式的七类 tabx 数据位于 `local_data/tabx`，文件仍为 `cards_0.113.1.json`、`powers_0.113.1.json` 等。`extract` 命令仅生成本地数据，不运行分类脚本。

所有非测试模型都会进入本地数据，包含废弃和未注册条目。抽象类只用于继承解析。普通事件与先古之民分开保存，`get_data_by_api('events')` 同时提供先古之民遗物关联，`data['events']` 仍是普通事件列表。

提取器解释构造器、动态变量、升级方法和文本模板，不启动游戏，也不执行反编译代码。卡牌输出基础版和一次升级版；支持反复升级的卡牌仍保留最大升级等级。展示采用战斗外、无外部加成、已揭示名称的上下文；实验体使用初始编号。依赖具体施加者或游戏状态而无法还原的字段会报告，不能假定为 0。

## 保留 Wiki 人工内容

首次迁移前，需要保留卡牌的 `page/beta_image/predecessor`、遗物的 `page/predecessor`，以及怪物的 `tier/power/stage/note/page/icon`。将完整 Wiki tabx JSON 放入配置指定的快照目录，默认使用 `Card_BETA.tabx.json`、`Relic_BETA.tabx.json`、`Power_BETA.tabx.json`、`Monster_BETA.tabx.json`；已有 `cards_<版本>.json` 等导出格式也可放入该目录。页面名清单可保存为 `page_names.json`（字符串数组）。

也可在有网络和 Wiki 凭据时显式保存一次快照：

```powershell
# 通过环境变量设置 HUIJI_WIKI_USERNAME、HUIJI_WIKI_PASSWORD、HUIJI_WIKI_STS2_KEY
.\.venv\Scripts\python.exe -m local_game_data snapshot-wiki
```

该命令只读取 Wiki；正常 `extract/export` 不会调用它。随后可以断网导出。使用其他快照目录：

```powershell
.\.venv\Scripts\python.exe -m local_game_data export --version 0.113.1 --baseline D:\wiki-baseline
```

每个 tabx 都有同名 `.report.json`。只有 `ready_to_publish: true` 才表示该表通过提取完整性和基线检查；这不替代人工审核。缺口条目有基线时保留旧行，没有基线时暂不进入 tabx；原始模型及具体原因始终保留在本地数据和报告中。旧条目不会因本次解析失败或源码缺失被自动删除。

卡牌、遗物、怪物缺少 Wiki 基线时会标记为候选数据，避免把页面名、前作对应条目和人工备注当作已核对内容。空值、字符串 `"None"` 和已有自定义图片名都会按基线保留。不会从本作名称猜测前作对应条目。

若只有从 Wiki 复制的部分数据行，可在本地基线 JSON 顶层增加 `"_partial": true`。已知行的人工字段仍会保留，但报告中的 `baseline_partial` 会是 `true`，结果继续标记为候选；此标记不会进入输出表。用完整 Wiki 快照替换后即可解除片段标记。

## 当前 Wiki 表格格式

默认目标为 `Data:Card_BETA.tabx`、`Data:Relic_BETA.tabx`、`Data:Power_BETA.tabx`、`Data:Monster_BETA.tabx`；其他三类维持原页面名，均可在 `wiki.tables` 中指定。

- 卡牌按 Wiki 的 16 列输出，包含 `beta_image` 和 `predecessor`，`multiplayer_only` 位于 `page` 之后，值为 `是/否`；分类为 `card_beta`，表头的中文标题与字段名一致。
- 遗物按 13 列输出，末列为 `predecessor`，分类为 `relic_beta`；`predecessor` 的中文标题为字段名，其余中文标题为空。
- 怪物按 14 列输出，末列为 `icon`，分类为 `monster_beta`。
- 状态保持 9 列，分类为 `power`。`allow_negative` 按源码输出字符串 `True/False`。

无已有值时，卡牌 `beta_image` 默认使用字符串 `"None"`，`predecessor` 和怪物 `icon` 默认是 JSON `null`。四张表的顶层说明和表头与维护者提供的 Wiki 格式对应；状态表的 `sources` 使用本次版本。输出文件名继续采用 `cards_<版本>.json` 等形式，对应目标 Wiki 页写入同名报告的 `wiki_page` 字段。

## 使用旧脚本

生成本地数据后，仍可直接运行 `card/update_card.py` 等原入口。默认从脚本目录的 `local_data/dataset.json` 读取，也可设置 `STS2_DATASET` 指向其他文件，`STS2_WIKI_BASELINE` 指定快照目录。

`utils` 导入时不会登录 Wiki；显式使用 `site` 才初始化连接。`parse_tabx` 优先读本地快照，普通联网维护调用在没有快照时仍可读取 Wiki；`STS2_OFFLINE=1` 会禁止 Wiki 连接。上传和页面维护功能继续使用原有 Wiki 接口。

`update_card_images` 保留为完整卡牌成图上传工具，用 `STS2_CARD_IMAGES` 指定包含 `card-images` 的目录。GDRE 恢复的原画不能当作完整卡牌截图上传。

Spine 上传脚本默认读取本次恢复目录的 `animations`，也可用 `STS2_ANIMATIONS` 指定其他本地目录；转换器仍按原脚本约定放在 `anim` 目录。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

测试包含断网生成七类表、与维护者提供的 BETA 表 schema 逐列对照、配置覆盖与凭据环境变量、实际卡牌升级与怪物数据，以及解析失败时保留旧行和人工字段。真实源码不会提交到此仓库；没有相邻的恢复目录时，真实数据集成测试会跳过。生成数据、Wiki 快照、个人配置、凭据和游戏资源均不提交。
