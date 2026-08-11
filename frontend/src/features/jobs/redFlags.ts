/** Chinese label for each red-flag type (Phase 3 risk lens). */
export const RED_FLAG_LABEL: Record<string, string> = {
  training_loan: "培训贷",
  training_fee: "岗前培训费",
  outsourcing_onsite: "外包驻场",
  inflated_salary: "薪资虚高",
  long_term_listing: "常年挂单",
  other: "其他风险",
};

/** Tag color by red-flag severity. */
export const RED_FLAG_COLOR: Record<string, string> = {
  high: "red",
  medium: "orange",
  low: "gold",
};
