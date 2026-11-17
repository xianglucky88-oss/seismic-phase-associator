# 审查与回滚手册

本手册给出相位/事件数据契约变更时**可执行的**审查与回滚步骤。所有命令均可在当前仓库直接运行，不依赖网络或凭据。

适用对象：字段语义变更、关联规则/窗口变更、台站校正变更、事件身份/版本策略变更、以及人工撤回与目录合并的操作。

> 前置原则：本次维护**不新增关联算法**。若某项变更需要新算法，它不在本手册范围内，应单独立项评审。

---

## 1. 变更分级

| 级别 | 触发条件 | 最低审查 |
|---|---|---|
| **L0 文档** | 只改 `docs/`，不改代码 | 一人复核字段描述与代码一致 |
| **L1 表现层** | 只增输出字段，不改变 `events`/`rejected_pick_count` | 一人复核 + 全量样例回放无差异 |
| **L2 行为变更** | 改变 `origin_time`/台站集/`pick_count`/`rejected_pick_count` | 两人复核 + 全量样例回放 + 登记册新增行 |
| **L3 身份/版本策略** | 改变 `event_id` 语义、引入稳定身份或版本号 | 两人复核 + 下游引用点全部盘点 + 登记册 §3/§4 双登记 + 明确回滚点 |

`event_id` 语义变更**一律**为 L3，即使看起来只是「换个格式」。

## 2. 审查步骤（每次变更照做）

### 2.1 建立基线

```bash
git rev-parse HEAD                       # 记录 code_ref
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 tools/scenario_report.py > /tmp/before.txt
```

`unittest` 必须全绿；`scenario_report.py` 的输出即「当前行为快照」。

### 2.2 判定受影响字段

对照 `docs/DATA_CONTRACT.md` 的状态标签：

- 改动的是 `[已实现]`/`[已钉住]` 字段 → **L2 起**；
- 改动只涉及 `[未实现]` 概念 → 先确认是否只是文档，若是则 L0；
- 涉及 `event_id` → **L3**。

### 2.3 全量回放

```bash
# 行为快照对比
PYTHONPATH=src python3 tools/scenario_report.py > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt

# 逐样例语义差异（推荐，能区分「改了什么」而不是「文本不同」）
for pair in \
  "late_pick/before late_pick/after" \
  "duplicate/replay duplicate/phantom_event" \
  "retraction/before retraction/collapse" \
  "merge/before_a merge/merged" \
  "clock_correction/before clock_correction/split_event" \
  "association/greedy_drop association/greedy_shift"; do
  set -- $pair
  echo "## $1 -> $2"
  PYTHONPATH=src python3 tools/replay_diff.py \
    fixtures/scenarios/$1.json fixtures/scenarios/$2.json
done
```

`replay_diff.py` 的退出码：`0` 无差异、`1` 有差异、`2` 输入/运行失败。CI 中可用 `--strict` 让任何差异都失败。

### 2.4 台站配置专项审查

时钟校正是最容易被误判为「只改时间」的变更。专项步骤：

```bash
# 1) 准备候选校正表 new_clock.json
# 2) 对同一份输入比较旧/新校正下的行为
PYTHONPATH=src python3 tools/replay_diff.py \
  fixtures/scenarios/clock_correction/before.json \
  fixtures/scenarios/clock_correction/before.json \
  --new-corrections new_clock.json
```

必须明确回答：

- 是否只改了 `origin_time`？（`shift_origin` 型）
- 是否改了台站集 / `pick_count`？（成员变化）
- 是否导致事件消失或拆分？（`split_event` 型，最严重）
- 校正表是否干净：是否有键指向输入中不存在的台站？（当前**不告警**，需人工核对待校正表的键是否都在输入台站集中）（当前**不告警**，需人工核对待校正表的键是否都在输入台站集中）

### 2.5 事件身份专项审查（L3）

1. 盘点所有 `event_id` 的外部引用点（下游库、报表、告警）。
2. 确认它们是否已改用 `event_ref = <catalog_version>:<event_id>`（`CATALOG_REGISTER.md` §2）。
3. 用 `merge/before_a`、`merge/before_b`、`merge/merged` 三份样例演示「同一 `evt-001` 指不同事件」的风险，作为评审记录附件。
4. 在登记册 §4 逐条登记被重排事件。

### 2.6 冻结审查记录

将以下内容作为评审附件存档：

- `git rev-parse HEAD` 与 `pyproject.toml` 版本；
- `/tmp/before.txt` 与 `/tmp/after.txt`；
- 每份 `replay_diff.py` 输出；
- 登记册新增行；
- 本次变更的分级与回滚点。

## 3. 审查清单（可勾选）

- [ ] 已记录 `code_ref` 与 `engine_version`
- [ ] 单元测试全绿
- [ ] `scenario_report.py` 前后快照已对比
- [ ] 每类场景 `replay_diff.py` 已运行并解释
- [ ] 变更分级正确（身份类是否误标为 L2）
- [ ] 台站校正变更已区分「时间」与「成员」
- [ ] 所有 `event_id` 外部引用已盘点
- [ ] 登记册 §3/§4 已更新
- [ ] 回滚点已写明且可执行
- [ ] 未引入新的关联算法（或已另行立项）

## 4. 回滚手册

### 4.1 回滚的前提：保留了什么

当前系统**不保留历史结果**，回滚依赖仓库外的归档。必须确保：

| 资产 | 位置 | 为什么必须保留 |
|---|---|---|
| 每次提交的**完整输入 JSON** | 外部归档 | 输出可由输入重算；被撤回/重复的 pick 只能从这里还原 |
| 每次发布的输出 JSON | 外部归档 | 用于证明已发布内容 |
| `code_ref` | 登记册 | 重算需要同一代码版本 |

若上述任一缺失，回滚只能「重新生成一个不同结果」，**不能**恢复原发布内容。

### 4.2 回滚决策

| 现象 | 处置 |
|---|---|
| 仅 `origin_time` 变化 | 用旧输入 + 旧 `code_ref` 重算，发布新版本并在登记册标 `retimed` |
| 事件成员变化（台站集/`pick_count`） | 同上，登记 `membership_changed` |
| 事件消失或拆分 | **立即回滚**：发布旧版本，登记 `removed`/`split_into`，回溯触发变更的校正/规则 |
| `event_id` 重排 | 回滚版本号，禁止用旧 `event_id` 继续引用；改发 `event_ref` |
| 目录合并后发现重复事件 | 用 `duplicate/` 场景确认是否为偏移重复；回滚合并并显式声明去重假设 |

### 4.3 执行回滚

```bash
# 1) 检出问题变更之前的 commit
git checkout <code_ref>

# 2) 用归档的原始输入重算（输入必须与当时完全一致）
PYTHONPATH=src python3 -m seismic_phase_associator.cli /archive/path/to/input.json > /tmp/rollback.json

# 3) 验证重算结果与归档的已发布输出一致
python3 - <<'EOF'
import json
a = json.load(open("/tmp/rollback.json"))
b = json.load(open("/archive/path/to/published.json"))
print("identical:", a == b)
EOF

# 4) 若不一致，说明归档输入或代码版本不匹配 —— 停止，不要发布
```

### 4.4 回滚后的登记

- 新增一行登记（`影响类别` 含 `S` 及实际变化类别）；
- 在 §4 表把受影响事件标为回滚目标版本；
- 记录回滚原因、根因、以及防止复发的检查项（追加到 §3 清单）。

### 4.5 不可回滚的情形

明确列出，避免误判：

| 情形 | 为什么不可回滚 | 补救 |
|---|---|---|
| 输入归档丢失 | 输出无法重算 | 无法恢复，只能发布新版本并声明断档 |
| 被撤回 pick 的原值未归档 | 输入里已不含它 | 无法恢复 |
| 下游已按裸 `event_id` 建立外键 | 身份已漂移 | 无法自动修正，需人工重建映射 |
| 震级来自外部系统且未记录来源 | 流水线不产生震级 | 无法回溯，需向外部系统取证 |

## 5. 与文档的联动

- 字段契约变化 → 更新 `DATA_CONTRACT.md` 并核对其标签；
- 新增/修改样例 → 更新 `SCENARIOS.md` 的 `scenario_report.py` 输出块；
- 版本/身份策略变化 → 更新 `CATALOG_REGISTER.md` §2 规则与 §7「已知无法登记的情形」。

## 6. 禁止事项

- 禁止为了让回放「看起来一致」而修改归档输入。
- 禁止在未登记的情况下发布 L2 及以上变更。
- 禁止用 `event_id` 作为跨运行外键。
- 禁止用 `pick_count`/台站数冒充「置信度」。
- 禁止在本次维护范围内新增关联算法来「修复」上述边界——那些是目标契约，需单独立项。
