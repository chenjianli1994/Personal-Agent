# SWE.3 C 源码

- Requirement: THM-SWE-006
- Decision: decision_20260615_110628_7d7012
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


## Candidate C Implementation

The following C/H content is generated as a candidate implementation for THM-SWE-006.

```c
#include <stdbool.h>
#include <stdint.h>

#define THERMAL_HIGH_THRESHOLD_C      85
#define THERMAL_RECOVERY_THRESHOLD_C  80
#define THERMAL_DERATE_PERCENT        30u
#define THERMAL_SENSOR_FAULT_DERATE   50u

typedef struct
{
    int16_t coolant_temp_c;
    bool sensor_valid;
} ThermalMgr_Input;

typedef struct
{
    bool fan_enable;
    bool pump_enable;
    bool thermal_fault;
    bool derate_request;
    uint8_t derate_percent;
} ThermalMgr_Output;

static bool ThermalMgr_IsHighTemperature(int16_t coolant_temp_c)
{
    return coolant_temp_c >= THERMAL_HIGH_THRESHOLD_C;
}

void ThermalMgr_Evaluate(const ThermalMgr_Input *input, ThermalMgr_Output *output)
{
    if (output == 0)
    {
        return;
    }

    output->fan_enable = false;
    output->pump_enable = false;
    output->thermal_fault = false;
    output->derate_request = false;
    output->derate_percent = 0u;

    if ((input == 0) || (input->sensor_valid == false))
    {
        output->fan_enable = true;
        output->pump_enable = true;
        output->thermal_fault = true;
        output->derate_request = true;
        output->derate_percent = THERMAL_SENSOR_FAULT_DERATE;
        return;
    }

    if (ThermalMgr_IsHighTemperature(input->coolant_temp_c))
    {
        output->fan_enable = true;
        output->pump_enable = true;
        output->derate_request = true;
        output->derate_percent = THERMAL_DERATE_PERCENT;
        return;
    }

    if (input->coolant_temp_c >= THERMAL_RECOVERY_THRESHOLD_C)
    {
        output->fan_enable = true;
        output->pump_enable = true;
    }
}
```

## Candidate Boundary

- This code is a candidate artifact only.
- It must pass Gate findings and human review before any approved baseline update.