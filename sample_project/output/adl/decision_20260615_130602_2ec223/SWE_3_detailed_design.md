# SWE.3 软件详细设计

- Requirement: THM-SWE-006
- Decision: decision_20260615_130602_2ec223
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


## Candidate Detailed Design

- Requirement scope: THM-SWE-006 thermal management behavior.
- Inputs: coolant temperature and sensor_valid.
- Outputs: fan_enable, pump_enable, thermal_fault, derate_request, derate_percent.
- Safety rule: invalid sensor input enters safe cooling and derate state.
- Threshold rule: temperature >= 85C enables cooling and derate.
- Recovery rule: temperature >= 80C keeps fan and pump active without derate.