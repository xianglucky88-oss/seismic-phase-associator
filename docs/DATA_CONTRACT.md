# 相位与事件数据契约

本文件是台站时钟、P/S 相位、事件关联、人工复核、拆分/合并与目录版本之间的**数据契约**。它同时是字段/版本策略变更的影响评审基线。

## 0. 阅读约定：三种状态标签

本文每一处行为都带以下标签之一。**不得**把标签理解成承诺强度，它们只描述证据来源。

| 标签 | 含义 | 谁可以依赖 |
|---|---|---|
| `[已实现]` | 当前代码按此行为运行，可用现有 CLI/fixture 直接复现 | 下游系统、回归测试 |
| `[已钉住]` | 行为由 `tests/test_scenarios.py`、`tests/test_tools.py` 钉死，改动会直接让测试失败 | 变更评审、CI |
| `[未实现]` | 概念在本文中命名并被分配了责任边界，但当前代码**没有**对应字段或逻辑；写入描述的是目标契约 | **没有任何系统可以依赖** |

> 重要：当前仓库是一个确定性基线。任务词汇中的「置信度」「未关联原因」「事件稳定身份」「震级来源」「目录版本/修订」在代码中**均不存在**。本文为它们定义责任边界，但绝不谎称已实现。凡未带 `[已实现]`/`[已钉住]` 的字段，接入方必须视为不存在。

---

## 1. 当前实现的输入契约 `[已实现]`

CLI 入口：`src/seismic_phase_associator/cli.py`；编排：`pipeline.py:5`。

```jsonc
{
  "picks": [
    {"station": "STA01", "phase": "P", "time": 10.02, "magnitude": 2.1}
  ],
  "clock_corrections": {"STA01": -0.02}
}
```

| 字段 | 类型 | 必需 | 当前行为 | 证据 |
|---|---|---|---|---|
| `picks` | list | 否（缺省等价空） | 缺键→空输入；非 list→`ValueError` | `parser.py:2-4`，`test_missing_picks_key_is_empty_input` |
| `picks[]` 元素 | dict | — | 非 dict 行被**静默丢弃**，且不计入 `rejected_pick_count` | `parser.py:5`，`test_non_dict_rows_are_dropped_before_counting` |
| `picks[].station` | str | 是 | 缺失→`KeyError`。类型**未校验**：全为同类型非 str（如全 int）可运行并把非字符串写进输出；混合类型（str 与 int/bool/None）在 `sorted` 时→`TypeError` | `engine.py:3,6,12` |
| `picks[].phase` | str | 否 | **完全不参与关联**，仅原样保留在内部 pick 中 | `engine.py` 全程未读 `phase`；`test_phase_is_not_used_for_association` |
| `picks[].time` | number | 是 | 缺失→`KeyError`；经 `float()` 强制转换，数值字符串可用；`NaN/Inf` 被接受但无法成形事件 | `engine.py:3` |
| `picks[].magnitude` | number | 否 | **不传播到输出**，不参与任何计算 | `test_magnitude_is_not_propagated` |
| `clock_corrections` | dict | 否（缺省 `{}`） | 键为台站名，值为秒偏移；未知台站键被忽略；`null`→`AttributeError` | `engine.py:2-3` |

附加事实 `[已钉住]`：

- 时间先经 `round(t + correction, 3)`，再参与比较（`engine.py:3`）。
- 输出对**输入行顺序不变**（按修正时间排序；含并列与重复台站的情形已穷举验证）：`test_output_is_invariant_to_input_row_order`。
- `time` 一律经 `float()` 提升，整型与浮点混用无差异（`test_int_and_float_times_are_equivalent`）。
- `station` 的类型**未校验**：全 int 可运行且输出非字符串台站名；str 与 int 混用则在 `sorted` 处 `TypeError`。这是输入契约缺口，校验责任目前不在本仓库。

## 2. 当前实现的输出契约 `[已实现]`

`engine.py:11-12`，外层信封 `report.py:5`。

```jsonc
{
  "project": "seismic-phase-associator",
  "status": "ok",
  "events": [
    {"event_id": "evt-001", "origin_time": 10.0,
     "stations": ["STA01","STA02"], "pick_count": 2,
     "source_stations": ["STA01","STA02"]}
  ],
  "rejected_pick_count": 0
}
```

| 字段 | 当前语义 | 证据 |
|---|---|---|
| `event_id` | `evt-{创建序号:03d}`，**按事件创建顺序**分配，与物理事件无关 | `engine.py:8` |
| `origin_time` | 该事件**第一个**（修正后时间最小）pick 的时间；之后不再更新 | `engine.py:8` |
| `stations` / `source_stations` | 当前版本恒相等，均为去重后排序的台站名 | `engine.py:12`，`test_pick_count_equals_station_count_when_accepted` |
| `pick_count` | 恒等于 `len(stations)`：每个台站在一个事件内最多一个 pick | `engine.py:6,10` |
| `rejected_pick_count` | `修正后 pick 总数 − 已接受事件中的 pick 数`；是一个**减法余额**，不是逐 pick 的判定结果 | `engine.py:12` |
| `status` | 恒为 `"ok"`；不存在 `partial`/`error` 等值 | `report.py:5` |

`[未实现]` 且**不得**依赖的输出字段：`magnitude`、`confidence`、`association_status`、`unassociated_reason`、`phase`、`catalog_version`、`revision`、`created_at`。

## 3. 拾取身份（Pick Identity）

这是责任边界最容易混淆的地方。

| 概念 | 现状 | 状态 |
|---|---|---|
| pick 的**唯一标识** | 不存在。pick 无 ID；同一台站同一相位的多个 pick 只能靠行位置区分 | `[未实现]` |
| pick 的**去重键** | 不存在。重复投递若时间相同，靠「同台站不能二次入事件」被挡在计数里；若时间不同，会生成第二个事件 | `[已实现]`（见 §5 与 `duplicate/` fixtures） |
| pick 的**相身份** | `phase` 字段被读取但从不用于判定；P 与 S 对关联完全等价 | `[已钉住]` |

**责任边界结论**：当前系统无法回答「这条 pick 是不是我刚才已发布的那一条」。任何需要 pick 级溯源、撤回或幂等重放的流程，都**必须**在系统外维护，不能依赖本仓库输出。

> 拾取身份的目标契约 `[未实现]`：建议引入 `pick_id`（来源系统 + 台站 + 相位 + 精确到毫秒的时间 + 版本），作为去重与撤回的唯一键；`phase` 参与关联规则并进入输出。在实现前，本文所有「重复」「撤回」示例只能通过**整份输入重放**来模拟。

## 4. 台站校正（Station Correction）

| 事项 | 现状 | 状态 |
|---|---|---|
| 校正来源 | 仅请求内 `clock_corrections`；无台站表、无生效时间、无历史 | `[已实现]` |
| 校正对象 | 逐 pick 加到 `time` 上，随后 `round(...,3)` | `[已实现]` |
| 未知台站 | 偏移按 0 处理，且**不告警** | `[已实现]` |
| 校正负值 | 允许；可把时间推到负数，仍正常关联 | `[已实现]` |
| 校正对关联的副作用 | 会改变排序位置、事件成员、`origin_time`，甚至**拆分事件** | `[已钉住]`（`clock_correction/` fixtures） |
| 校正的**人工复核责任** | 未定义：谁批准、何时生效、哪些历史结果需重算 | `[未实现]` |

台站配置变更的样例影响见 `SCENARIOS.md` §5。关键结论：**时钟校正不是「只移动时间戳」**，它同时是事件成员变更与 ID 重排的触发器。

## 5. 关联规则（当前唯一规则）`[已实现]`

完整逻辑共三行（`engine.py:5-11`）：

1. 按修正时间升序遍历 pick。
2. 对每个 pick，取**第一个**满足
   `|event.origin_time − pick.time| ≤ 0.25` 且 `pick.station ∉ event.stations`
   的已存在事件；无则新建事件，`origin_time = pick.time`。
3. 只有 `台站数 ≥ 2` 的事件被接受；其余事件整体丢弃。

由此产生的、必须在评审中显式承认的**边界行为**：

- **首匹配而非最近匹配**：`next(...)` 取创建顺序中第一个合格事件（`engine.py:6`）。
- **0.25 秒为闭区间**；等于 0.25 仍关联。
- **`origin_time` 不随后续 pick 更新**，因此时间是**非传递的**：0、0.25、0.5 三个 pick 不会被归并成一个事件。
- **失败事件的 pick 不会被重新考虑**：一个 pick 若绑定到后来被拒的单台站事件，就随之消失，`rejected_pick_count` 是唯一痕迹（`association/greedy_drop.json`）。
- **同一事件内每台站至多一个 pick**：同台站的 P 与 S 不能共存于一个事件。
- **被拒事件仍消耗 `event_id` 序号**：首个事件若被拒，输出可能从 `evt-002` 开始（`test_rejected_event_consumes_an_id`）。

> 明确的维护边界：本次交接**不新增、不修改关联算法**。上列行为是待评审的既有事实，不是待修复项的授权。

## 6. 置信度（Confidence）

| 事项 | 现状 |
|---|---|
| `confidence` 字段 | `[未实现]` |
| 任何形式的质量分/权重 | `[未实现]` |
| 关联「可信」的唯一代理 | 台站数 ≥ 2 的二值接受规则 |

**责任边界**：不得把 `pick_count`、台站数或 `rejected_pick_count` 解释为置信度。它们只是计数。若下游需要排序或阈值，必须在目标契约中显式引入 `confidence` 并定义其来源（台站数、走时残差、相位组合等），且需要**版本化**，否则每次重跑都会产生无法比较的分数。

## 7. 未关联原因（Unassociated Reason）

当前只有**一个数字**：`rejected_pick_count`。没有任何 per-pick 的拒绝原因。代码里事实上存在三类拒绝，但都被合并进同一个减法余额：

| 实际类别 | 系统是否可区分 | 说明 |
|---|---|---|
| 单台站事件被拒（孤立/离群 pick） | 否 | 事件最终只有 1 个台站 |
| 同台站重复 pick 被挡 | 否 | `station ∈ event.stations` 导致无法入事件 |
| 输入行非 dict | 否（更糟：根本不计数） | `parser.py:5` 静默丢弃 |

**责任边界**：`rejected_pick_count > 0` 时，系统**不**能告诉你哪条 pick、为什么、是否可挽回。需要「未关联原因」的下游流程必须视为 `[未实现]`。目标契约 `[未实现]` 建议字段：`pick_id`、`reason ∈ {isolated, duplicate_station, duplicate_pick, invalid_row, out_of_window}`、`candidate_event_ids`、`decided_at`。

## 8. 事件稳定身份（Event Stable Identity）

这是本次交接的**最高风险边界**。

| 事项 | 现状 | 状态 |
|---|---|---|
| `event_id` 是否跨运行稳定 | **否**。按创建序号分配；更早的迟到 pick 会让所有后续事件整体平移 | `[已钉住]`（`merge/`、`late_pick/` fixtures） |
| `event_id` 是否跨目录合并稳定 | **否**。两个目录各自都可能有 `evt-001`，合并后原 `evt-001` 可能变成 `evt-002` | `[已钉住]`（`merge/before_a.json` vs `before_b.json` vs `merged.json`） |
| 是否存在修订链/版本号 | 否 | `[未实现]` |
| 是否存在「同一物理事件」的持久键 | 否 | `[未实现]` |

**责任边界结论**：`event_id` 只能作为**单次运行内的行键**，禁止作为跨运行、跨目录、跨版本的外键。任何需要长期引用的系统（目录库、告警、对外发布）必须自行生成并维护稳定身份。

> 目标契约 `[未实现]`：建议以 `(稳定来源事件键)` 或 `(origin_time 舍入 + 台站集合指纹)` 生成内容寻址 ID，并附 `catalog_version` 与 `supersedes`。

## 9. 震级来源（Magnitude Source）

| 事项 | 现状 |
|---|---|
| 输入 `magnitude` | 被读取进内部 pick，但**从不输出**、从不参与计算 `[已实现]` |
| 事件级震级 | 不存在 `[未实现]` |
| 震级类型/台站/算法/作者 | 全部不存在 `[未实现]` |

**责任边界**：当前输出中**没有震级**。任何下游若展示了震级，其来源必然不是本流水线，必须单独标注出处。目标契约 `[未实现]` 建议：`magnitude.value`、`magnitude.type`（如 `ML/Mw`）、`magnitude.source_pick_ids`、`magnitude.agency`、`magnitude.revision`。由于当前无「震级来源」字段，**无法**回答「这个震级来自哪次修订」——这正是需要优先补齐的边界。

## 10. 目录版本与修订登记 `[未实现]`

代码中没有任何 `catalog_version`、`revision`、`supersedes`、`published_at`。当前唯一的事实是：**相同输入产生相同输出**（确定性），不同输入之间没有任何版本关系。

修订登记册的形式与责任划分见 `CATALOG_REGISTER.md`。该登记册是**系统外的人工/流程资产**，因为代码无法生成它。

## 11. 字段/版本策略变更的影响评审清单

在改动任何字段或版本策略前，必须能回答：

1. 该字段当前是 `[已实现]`、`[已钉住]` 还是 `[未实现]`？改的是行为还是只改文档？
2. 若改动 `event_id` 语义：所有跨运行引用点是否已改为稳定键？（见 §8）
3. 若改动关联窗口/规则：用 `tools/replay_diff.py` 对 `fixtures/scenarios/` 全量回放，列出受影响事件。
4. 若改动台站校正：区分「仅时间戳变化」与「事件成员/ID 变化」两类影响。
5. 若新增 `confidence`/`unassociated_reason`/`magnitude`：必须先定义版本与来源，再输出；否则重跑结果不可比较。
6. 更新 `docs/CATALOG_REGISTER.md` 中的对应条目，并给出回滚点（`docs/REVIEW_AND_ROLLBACK.md`）。

可执行的最小影响评审见 `SCENARIOS.md` 与 `tools/replay_diff.py`。

---

## 附：文档与代码的对应关系

| 文档 | 作用 | 是否可执行验证 |
|---|---|---|
| `DATA_CONTRACT.md`（本文） | 字段与责任边界 | 字段行为由 `tests/test_scenarios.py` 钉住 |
| `SCENARIOS.md` | 五类最小样例及其受影响历史结果 | `fixtures/scenarios/` + `tools/scenario_report.py` |
| `CATALOG_REGISTER.md` | 目录修订登记册模板与规则 | 模板，人工维护 |
| `REVIEW_AND_ROLLBACK.md` | 审查与回滚手册 | 命令可执行，流程人工执行 |
