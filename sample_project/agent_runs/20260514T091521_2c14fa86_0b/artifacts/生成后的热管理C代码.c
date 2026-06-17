#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool charging;
    int16_t ambient_temp_c;
    int16_t coolant_temp_c;
    bool sensor_valid;
} ThermalInput;

typedef struct {
    bool pump_on;
    bool fan_on;
    uint8_t fan_speed_percent;
} ThermalOutput;

ThermalOutput ThermalControl_Update(ThermalInput input)
{
    ThermalOutput output = {false, false, 0U};
    if (!input.sensor_valid) {
        output.pump_on = false;
        output.fan_on = true;
        output.fan_speed_percent = 40U;
        return output;
    }
    if (input.charging && input.coolant_temp_c >= 85) {
        output.pump_on = true;
    }
    if (input.ambient_temp_c >= 35 || input.coolant_temp_c >= 85) {
        output.fan_on = true;
        output.fan_speed_percent = 70U;
    }
    return output;
}
