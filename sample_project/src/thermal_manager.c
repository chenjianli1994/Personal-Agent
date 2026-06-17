#include "thermal_manager.h"

ThermalManagerOutput ThermalManager_Step(const ThermalManagerInput *input)
{
    ThermalManagerOutput output = { .derate_active = false, .cooling_percent = 0U };

    if (input == 0 || !input->sensor_valid) {
        output.derate_active = true;
        output.cooling_percent = 100U;
        return output;
    }

    if (input->battery_temp_c > 60) {
        output.derate_active = true;
        output.cooling_percent = 80U;
    }

    return output;
}
