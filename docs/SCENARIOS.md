# 最小样例：哪些历史结果会受影响

本文件给出台站时钟、相位、关联、复核、拆分/合并、目录版本六类维护动作的**最小可运行样例**。每个样例都指向 `fixtures/scenarios/` 下的文件，数字由 `tools/scenario_report.py` 从当前代码重新生成，并由 `tests/test_scenarios.py` 钉住。

## 如何复现

```bash
# 单份输入
PYTHONPATH=src python3 -m seismic_phase_associator.cli fixtures/scenarios/late_pick/after.json

# 全量样例表（以当前代码为准）
PYTHONPATH=src python3 tools/scenario_report.py

# 两份输入的行为差异（变更评审）
PYTHONPATH=src python3 tools/replay_diff.py OLD.json NEW.json
```

`scenario_report.py` 的当前输出（`event_id@origin_time[stations]xpick_count`）：

```
late_pick/before.json                rejected=0  evt-001@10.0[STA01,STA02]x2
late_pick/after.json                 rejected=1  evt-001@9.8[STA01,STA03]x2
duplicate/replay.json                rejected=1  evt-001@10.0[STA01,STA02]x2
duplicate/phantom_event.json         rejected=0  evt-001@10.0[STA01,STA02]x2; evt-002@10.0[STA01,STA02]x2
retraction/before.json               rejected=0  evt-001@10.0[STA01,STA02,STA03]x3
retraction/after.json                rejected=0  evt-001@10.0[STA01,STA03]x2
retraction/collapse.json             rejected=1  (none)
merge/before_a.json                  rejected=0  evt-001@10.0[STA01,STA02]x2
merge/before_b.json                  rejected=0  evt-001@20.0[STA03,STA04]x2
merge/merged.json                    rejected=0  evt-001@10.0[STA01,STA02]x2; evt-002@20.0[STA03,STA04]x2
merge/shared_station.json            rejected=1  evt-001@10.0[STA01,STA02,STA03]x3
clock_correction/before.json         rejected=0  evt-001@10.0[STA01,STA02,STA03]x3
clock_correction/shift_origin.json   rejected=0  evt-001@10.1[STA01,STA02,STA03]x3
clock_correction/split_event.json    rejected=2  (none)
association/greedy_drop.json         rejected=1  evt-002@10.3[STA02,STA03]x2
association/greedy_shift.json        rejected=1  evt-001@10.06[STA01,STA02]x2
```

---

## 1. 迟到拾取（Late Pick）

**问题**：一个比已发布事件更早到达的 pick，会不会只是「补进来」？
**答案**：不会。它会重写事件，并驱逐已有成员。

| 输入 | 结果 | 受影响的历史结果 |
|---|---|---|
| `late_pick/before.json` | `evt-001 @ 10.0, {STA01,STA02}, 2`，`rejected=0` | 基线（可视为已发布版本） |
| `late_pick/after.json` | `evt-001 @ 9.8, {STA01,STA03}, 2`，`rejected=1` | **`origin_time` 从 10.0 后退到 9.8**；台站集变化；原先已关联的 STA02 变成未关联 |

**为什么会驱逐 STA02**：排序后 STA03(9.8) 先创建事件，STA01(10.0) 距 9.8 仅 0.2 s 并入，STA02(10.1) 距新原点 0.3 s > 0.25 s，无法并入，成为被拒单台站事件。

**受影响的下游判断**：已按 `origin_time` 发布的地震速报、按台站集合生成的质量标记、任何引用 `evt-001 @ 10.0` 的告警。注意 `event_id` 仍叫 `evt-001`，所以**引用不会断，但含义变了**——这是最隐蔽的影响。

复现差异：

```bash
PYTHONPATH=src python3 tools/replay_diff.py \
  fixtures/scenarios/late_pick/before.json fixtures/scenarios/late_pick/after.json
# event_changed: origin_time [10.0, 9.8], stations [[STA01,STA02],[STA01,STA03]]
# rejected_pick_count: [0, 1]
```

## 2. 重复消息（Duplicate Message）

**问题**：同一条 pick 收到两次，会怎样？
**答案**：取决于两次的时间是否完全相同——两种结果**都不正确**。

| 输入 | 结果 | 受影响的历史结果 |
|---|---|---|
| `duplicate/replay.json`（时间完全相同） | 1 个事件，`rejected=1` | 重复被吸收进计数，表面「正常」；但没有任何记录说明这 1 条是重复 |
| `duplicate/phantom_event.json`（整体偏移 0.04 s） | **2 个事件**，同一台站对，`rejected=0` | 一个物理事件被发布成两个：重复的速报、重复计数、震级/台站统计翻倍 |

**责任边界**：当前无 pick 级去重键（见 `DATA_CONTRACT.md` §3）。时间完全相同的重复靠「同台站不得二次入事件」被顺带挡住；时间哪怕差 1 ms，就会被当成新证据。**系统无法区分「重复投递」与「真实的新相位」**。

受影响历史结果：事件计数、台站统计、`rejected_pick_count` 的语义（它同时承担了「重复」和「未关联」两种含义）。

> 用 `replay_diff.py` 比较这两份输入会报告「新增 1 个事件 + 未关联余额变化」，**不会**报告重复本身——因为系统里没有「重复」这个类别。这正是无 pick 身份的直接后果。

## 3. 人工撤回（Manual Retraction）

**问题**：运营人员撤回一条错误 pick，用什么表达？
**答案**：当前**没有撤回字段**。唯一方式是「不带该 pick 重新提交整份输入」，即重放。

| 输入 | 结果 | 受影响的历史结果 |
|---|---|---|
| `retraction/before.json` | `evt-001 @ 10.0, {STA01,STA02,STA03}, 3` | 基线 |
| `retraction/after.json`（去掉 STA02） | `evt-001 @ 10.0, {STA01,STA03}, 2`，`rejected=0` | 事件仍在，但 `pick_count`/台站集变化 |
| `retraction/collapse.json`（只剩 STA01） | **事件消失**，`rejected=1` | 撤回一条 pick 导致**整条事件被删**，而非降级 |

**责任边界**：撤回的**记录责任**完全在系统外。代码不产生 tombstone、不产生 `supersedes`、不保留被撤回的 pick。若下游缓存了 `retraction/before` 的结果，没有任何机制提示它已失效。

受影响历史结果：目录条目消失、依赖该事件的编号/发布记录、以及「撤回为何没有生效」的审计链——因为重跑后 `status` 仍是 `ok`。

## 4. 目录合并（Catalog Merge）

**问题**：合并两个目录时，事件身份还成立吗？
**答案**：不成立。`event_id` 会重排，且可能静默丢 pick。

| 输入 | 结果 | 受影响的历史结果 |
|---|---|---|
| `merge/before_a.json` | `evt-001 @ 10.0, {STA01,STA02}` | 目录 A |
| `merge/before_b.json` | **`evt-001 @ 20.0, {STA03,STA04}`** | 目录 B——**同一个 `evt-001` 指另一个事件** |
| `merge/merged.json` | `evt-001 @ 10.0` **+ `evt-002 @ 20.0`** | B 的事件从 `evt-001` 变成 `evt-002` |
| `merge/shared_station.json` | 1 个事件 3 个 pick，`rejected=1` | 共享台站的合并**丢掉一条 pick**（第二个 STA01 无法入事件，它派生的单台站事件被拒） |

**受影响历史结果**：所有跨目录引用 `evt-001` 的外键都会静默指向错误事件；合并后清单总数可能少于两目录之和（`shared_station` 情形）。

**责任边界**：`event_id` 只能作为单次运行行键。稳定身份必须由下游另建，见 `DATA_CONTRACT.md` §8。

## 5. 台站配置变化（Station Configuration Change）

**问题**：修正某个台站的时钟，是否只是「把时间挪一下」？
**答案**：不是。它会改变排序、原点和事件成员。

| 输入 | 结果 | 受影响的历史结果 |
|---|---|---|
| `clock_correction/before.json` | `evt-001 @ 10.0, 3 站` | 基线（无校正） |
| `clock_correction/shift_origin.json`（`STA01 += 0.3`） | `evt-001 @ 10.1, 3 站` | **原点台站从 STA01 变成 STA02**；`origin_time` 变化；未来任何「原点台站」派生的属性（含震级来源）都会换绑 |
| `clock_correction/split_event.json`（`STA02 += 0.5`） | **无事件**，`rejected=2` | 一个已发布事件**被拆散并全部拒收** |

**受影响历史结果**：已发布的发震时刻、以及所有依赖「哪个台站是原点」的下游（震级、深度、发布时延）。注意 `shift_origin` 里台站集合没变、`rejected` 也没变，**只有 `origin_time` 变了**——最容易在评审中漏掉。

复现：

```bash
PYTHONPATH=src python3 tools/replay_diff.py \
  fixtures/scenarios/clock_correction/before.json \
  fixtures/scenarios/clock_correction/before.json \
  --new-corrections /path/to/new_clock.json
```

## 6. 关联的贪心边界（补充样例，支撑上面的结论）

| 输入 | 结果 | 含义 |
|---|---|---|
| `association/greedy_drop.json` | `evt-002 @ 10.3, {STA02,STA03}`，`rejected=1` | STA01 绑到一个后来被拒的单台站事件，于是**永久丢弃**，不会改投后续事件 |
| `association/greedy_shift.json` | `evt-001 @ 10.06, {STA01,STA02}`，`rejected=1` | 输入只差 0.06 s，发布出来的台站对就换了一组 |

这说明「受影响的历史结果」不只是时间戳，还包括**哪条 pick 被保留、哪条被丢弃**，且这个选择没有「最优」保证。

---

## 影响矩阵（供字段/版本策略评审直接引用）

| 变更类型 | `event_id` | `origin_time` | 台站集 | `pick_count` | `rejected_pick_count` | 现有测试能否发现 |
|---|---|---|---|---|---|---|
| 迟到拾取 | 可能平移 | **可变**（可后退） | 可变 | 可变 | 可变 | 是（`late_pick`） |
| 重复（同时间） | 否 | 否 | 否 | 否 | **变** | 是（`duplicate/replay`） |
| 重复（偏移） | **新增事件** | 否 | 否 | 否 | 否 | 是（`duplicate/phantom_event`） |
| 人工撤回 | 可能平移 | 可变 | **变** | **变** | 可变 | 是（`retraction`） |
| 目录合并 | **重排** | 否 | **变** | 可变 | 可变 | 是（`merge`） |
| 台站校正 | 可能平移 | **可变** | 可变 | 否 | **可变** | 是（`clock_correction`） |

> 这张表的用途：当有人提出「只改 timestamp 精度」或「只调窗口阈值」时，用对应行说明它实际会波及哪些列。`tools/replay_diff.py` 可以对该表逐项自动复核。
