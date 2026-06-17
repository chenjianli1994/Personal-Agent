# SWE.3 C 代码变更

- Requirement: THM-SWE-006
- Decision: decision_20260615_130128_7d7012
- Goal: Regenerate current core code and test candidates
- Rationale: Explicit UI command requested candidate regeneration for code/test core artifacts under Gate and review.
- Previous process status: candidate
- Resource snapshot: requirements=1, artifacts=3, knowledge=0
- Style profile: samples=0, confidence=0.0

## Regeneration Boundary

This regenerated output is a candidate work product. It is not an approved baseline and must pass Gate checks plus human review before use.

## Style And Evidence Contract

- Use imported knowledge-base code examples as the primary style reference when concrete code is generated.
- Preserve project C naming, indentation, RTE wrapper patterns, and test fixture shape from retrieved examples.
- Keep code/test changes traceable to the selected requirement and to SWE.3/SWE.4 evidence.

## Style Evidence


## Candidate Diff

```diff
+#define THERMAL_HIGH_THRESHOLD_C      85
+#define THERMAL_RECOVERY_THRESHOLD_C  80
+
+if ((input == 0) || (input->sensor_valid == false))
+{
+    output->fan_enable = true;
+    output->pump_enable = true;
+    output->thermal_fault = true;
+    output->derate_request = true;
+    output->derate_percent = THERMAL_SENSOR_FAULT_DERATE;
+    return;
+}
+
+if (ThermalMgr_IsHighTemperature(input->coolant_temp_c))
+{
+    output->fan_enable = true;
+    output->pump_enable = true;
+    output->derate_request = true;
+    output->derate_percent = THERMAL_DERATE_PERCENT;
+}
```

## Merge Boundary

- Diff content is candidate-only and must not be applied to the approved baseline without review.