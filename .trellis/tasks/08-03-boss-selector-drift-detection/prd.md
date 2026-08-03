# 选择器漂移自动检测

## Goal

在 prepare 阶段检查关键选择器是否仍然匹配真实 DOM，提前预警选择器漂移。BOSS 页面
结构变更时，选择器可能失效，导致分类逻辑无法正确识别页面状态。

## Confirmed Facts

- 当前 prepare 流程通过 `classify_page()` 检查页面状态，但如果关键选择器（如
  `MESSAGE_INPUT`、`FINAL_SUBMIT_BUTTON`）因页面改版而失效，分类器会返回 `unknown`
  而非明确的 `selector_drift`。
- `classify_page()` 已有 `SELECTOR_DRIFT` outcome，但触发条件是「必填锚点缺失」，
  不会主动检查所有关键选择器的可见性。
- P2-2（选择器后端下发）完成后，后端可以更灵活地控制选择器列表，漂移检测受益于此。

## Requirements

### R1. 关键选择器清单

定义一组「关键选择器」，在 prepare 阶段必须全部可见。至少包括：
- `MESSAGE_INPUT` — 消息输入框
- `FINAL_SUBMIT_BUTTON` — 最终提交按钮
- `RESUME_UPLOAD` — 简历上传控件（可选，视页面结构）

### R2. 漂移检测逻辑

在 prepare 流程中加入漂移检测步骤：
- 遍历关键选择器清单，逐个检查 `is_visible`
- 如果任何关键选择器不可见，返回 `selector_drift`（而非 `unknown`）
- `diagnostic_reference` 标注具体哪个选择器漂移

### R3. 不影响安全不变量

- 漂移检测不增加任何点击或填充操作（只读检查）
- 不影响「prepare never clicks final submit」不变量

## Acceptance Criteria

- [ ] 关键选择器清单已定义
- [ ] prepare 流程中加入漂移检测步骤
- [ ] 关键选择器不可见时返回 `selector_drift`
- [ ] `diagnostic_reference` 标注具体漂移的选择器
- [ ] 新增测试覆盖漂移检测场景
- [ ] 全部后端测试通过
- [ ] ruff clean

## Notes

- 需要编写 `design.md` 确定检测逻辑的插入点（在 `classify_page` 之前还是之后）。
- 参考 `docs/boss-communicate-testing-plan.md` 第 4 节 P2-4。
- 建议在 P2-2（选择器后端下发）完成后实施，受益于后端可控的选择器列表。
