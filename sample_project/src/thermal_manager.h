#ifndef THERMAL_MANAGER_H
#define THERMAL_MANAGER_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    int16_t battery_temp_c;
    bool sensor_valid;
} ThermalManagerInput;

typedef struct {
    bool derate_active;
    uint8_t cooling_percent;
} ThermalManagerOutput;

ThermalManagerOutput ThermalManager_Step(const ThermalManagerInput *input);

#endif
