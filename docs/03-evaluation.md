# 03 效果评估

## 测试结果

验证命令：

```powershell
python -m pytest --junitxml=examples/test-results.xml
```

2026-10-28 本地实际结果：`10 passed in 0.34s`。机器环境为 Python 3.12.4、pytest 7.4.4。机器可读报告位于 `examples/test-results.xml`。

## 验收点对应关系

| 验收点 | 实际输出 | Trace | 自动化测试 |
|---|---|---|---|
| 正常排期 | `examples/outputs/normal.json` | `examples/traces/normal.json` | `test_normal_schedule_saves_draft_without_publishing` |
| 素材信息缺失 | `examples/outputs/missing-metadata.json` | `examples/traces/missing-metadata.json` | `test_missing_metadata_blocks_before_slot_query` |
| 规则风险 | `examples/outputs/rule-risk.json` | `examples/traces/rule-risk.json` | `test_rule_risk_blocks_draft` |
| 时段冲突 | `examples/outputs/slot-conflict.json` | `examples/traces/slot-conflict.json` | `test_conflict_suggests_alternative_and_waits_for_confirmation` |
| 确认替代时段 | `examples/outputs/slot-confirmed.json` | `examples/traces/slot-confirmed.json` | `test_confirmed_alternative_is_rechecked_then_saved` |
| 重复提交 | `examples/outputs/duplicate-replay.json` | `examples/traces/duplicate-replay.json` | `test_duplicate_execution_returns_one_existing_draft` |
| 保存失败 | `examples/outputs/save-failure.json` | `examples/traces/save-failure.json` | `test_save_failure_is_not_reported_as_success` |
| 步数上限 | 无固定示例文件 | 测试内断言 | `test_step_limit_returns_explicit_incomplete_status` |

## 两条关键动态路径

冲突未确认路径的动作序列：

```text
read_material → check_rules → query_slots(18:00 occupied)
→ finish(pending_confirmation, suggest 19:00)
```

确认替代时段路径的动作序列：

```text
read_material → check_rules → query_slots(18:00 occupied)
→ query_slots(19:00 available) → save_draft → finish(completed)
```

测试 `test_changed_tool_observation_changes_next_action` 还会替换排期工具结果：同一个 18:00 请求在有冲突时等待确认，在清空冲突后改为保存草稿，证明下一动作依赖工具观察。

## 实际结果摘要

- 冲突场景建议 `19:00`，`draft_save.status` 为 `not_saved`，没有越权修改硬性要求。
- 确认场景先复查 `19:00`，再保存 `DRAFT-77166415B3`。
- 重复场景第二次返回 `already_exists` 和相同草稿 ID，状态文件只有一条记录。
- 保存失败场景返回 `task_status: failed` 和 `error_code: save_failed`。
- 所有输出均包含 `published: false`。

## 失败与限制

1. 当前环境没有模型密钥，仓库中的运行记录来自离线 Mock；因此只能证明流程、工具和边界，不能证明真实模型在未知输入上的决策效果。
2. API 模式尚未进行在线兼容性测试，不同服务可能不支持 `response_format: json_object`。
3. 合成规则只覆盖必填字段、版权状态和少量禁用宣传语，不代表真实内容安全体系。
4. 草稿存储适用于本地演示，不具备跨进程事务、鉴权和审计防篡改能力。
5. 替代时段按同日整点向后搜索，未覆盖优先级、素材时长重叠、跨天或多渠道容量。

提交前应使用目标模型至少复跑正常、冲突和风险三条路径，并把真实模型 Trace 单独保存到 `examples/traces/api-*`，不要覆盖 Mock 记录。
