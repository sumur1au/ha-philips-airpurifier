"""Philips Air Purifier climate (heater) platform."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import SWING_OFF, SWING_ON, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .const import HEATER_TYPES, SWITCH_OFF, SWITCH_ON, FanAttributes, PresetMode
from .entity import PhilipsAirPurifierEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .__init__ import PhilipsAirPurifierConfigEntry
    from .coordinator import PhilipsAirPurifierCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PhilipsAirPurifierConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the climate platform."""
    coordinator = entry.runtime_data
    model_config = coordinator.model_config

    async_add_entities(PhilipsHeater(coordinator, kind) for kind in HEATER_TYPES if kind in model_config.heaters)


class PhilipsHeater(PhilipsAirPurifierEntity, ClimateEntity):
    """Philips AirPurifier heater."""

    _attr_temperature_unit: str = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step: float = 1.0

    # Preset modes that should surface as HVACMode.AUTO. Some models (e.g.
    # CX3120) only expose PresetMode.AUTO_PLUS rather than PresetMode.AUTO.
    _AUTO_PRESET_MODES = (PresetMode.AUTO, PresetMode.AUTO_PLUS)

    def __init__(
        self,
        coordinator: PhilipsAirPurifierCoordinator,
        kind: str,
    ) -> None:  # pragma: no cover
        """Initialize the heater."""
        super().__init__(coordinator)

        model_config = coordinator.model_config

        self._description = HEATER_TYPES[kind]
        self._attr_translation_key = "pap"

        self._attr_unique_id = f"{coordinator.model}-{coordinator.device_id}-{kind.lower()}"

        self._preset_modes_map = model_config.preset_modes
        self._attr_preset_modes = list(self._preset_modes_map.keys())

        hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        if any(mode in self._preset_modes_map for mode in self._AUTO_PRESET_MODES):
            hvac_modes.append(HVACMode.AUTO)
        if PresetMode.VENTILATION in self._preset_modes_map:
            hvac_modes.append(HVACMode.FAN_ONLY)
        self._attr_hvac_modes = hvac_modes

        self._power_key = self._description[FanAttributes.POWER]
        self._temperature_target_key = kind.partition("#")[0]
        self._temperature_current_key = self._description[FanAttributes.TEMPERATURE]

        self._attr_min_temp = self._description[FanAttributes.MIN_TEMPERATURE]
        self._attr_max_temp = self._description[FanAttributes.MAX_TEMPERATURE]

        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

        if model_config.oscillation:
            self._oscillation_key = next(iter(model_config.oscillation))
            self._oscillation_modes = model_config.oscillation[self._oscillation_key]
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = [SWING_ON, SWING_OFF]
        else:
            self._oscillation_key = None
            self._oscillation_modes = None

    @property
    def target_temperature(self) -> int | None:
        """Return the target temperature."""
        return self._device_status.get(self._temperature_target_key)

    @property
    def current_temperature(self) -> float | None:
        """Return the current (measured) temperature.

        The NEW2 protocol reports this value in tenths of a degree
        (e.g. 225 -> 22.5 C), matching the conversion the sensor
        platform applies via _to_celsius_from_tenths for the same key.
        """
        value = self._device_status.get(self._temperature_current_key)
        if isinstance(value, (int, float)):
            return float(value) / 10
        return None

    @property
    def hvac_mode(self) -> HVACMode | None:  # pragma: no cover
        """Return the current HVAC mode."""
        if not self.is_on:
            return HVACMode.OFF
        if self.preset_mode in self._AUTO_PRESET_MODES:  # pragma: no cover
            return HVACMode.AUTO
        if self.preset_mode == PresetMode.VENTILATION:  # pragma: no cover
            return HVACMode.FAN_ONLY
        return HVACMode.HEAT

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:  # pragma: no cover
        """Set the HVAC mode of the heater."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        elif hvac_mode == HVACMode.AUTO:
            # Prefer plain AUTO if the model supports it, otherwise fall
            # back to AUTO_PLUS (e.g. CX3120).
            auto_preset = next(
                (mode for mode in self._AUTO_PRESET_MODES if mode in self._preset_modes_map),
                PresetMode.AUTO,
            )
            await self.async_set_preset_mode(auto_preset)
        elif hvac_mode == HVACMode.FAN_ONLY:  # pragma: no cover
            await self.async_set_preset_mode(PresetMode.VENTILATION)
        elif hvac_mode == HVACMode.HEAT:
            await self.async_set_preset_mode(PresetMode.LOW)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        for preset_mode, status_pattern in self._preset_modes_map.items():
            for k, v in status_pattern.items():
                if self._device_status.get(k) != v:
                    break
            else:
                return preset_mode
        return None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the heater."""
        preset_modes = self._attr_preset_modes or []
        if preset_mode not in preset_modes:
            return

        status_pattern = self._preset_modes_map.get(preset_mode)
        if status_pattern:  # pragma: no branch
            await self.coordinator.async_set_control_values(status_pattern)
            self._device_status.update(status_pattern)
            self._handle_coordinator_update()

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode."""
        if not self._oscillation_key or not self._oscillation_modes:
            return None

        value = self._device_status.get(self._oscillation_key)
        if value == self._oscillation_modes[SWITCH_OFF]:
            return SWING_OFF
        return SWING_ON

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the swing mode of the heater."""
        if not self._oscillation_key or not self._oscillation_modes or swing_mode not in (SWING_ON, SWING_OFF):
            return

        value = self._oscillation_modes[SWITCH_ON] if swing_mode == SWING_ON else self._oscillation_modes[SWITCH_OFF]

        await self.coordinator.async_set_control_value(self._oscillation_key, value)
        self._device_status[self._oscillation_key] = value
        self._handle_coordinator_update()

    @property
    def is_on(self) -> bool | None:
        """Return the device state."""
        return self._device_status.get(self._power_key) != self._description[FanAttributes.OFF]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the device."""
        await self.coordinator.async_set_control_values({self._power_key: self._description[FanAttributes.ON]})
        self._device_status[self._power_key] = self._description[FanAttributes.ON]
        self._handle_coordinator_update()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the device."""
        await self.coordinator.async_set_control_values({self._power_key: self._description[FanAttributes.OFF]})
        self._device_status[self._power_key] = self._description[FanAttributes.OFF]
        self._handle_coordinator_update()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = int(kwargs.get(ATTR_TEMPERATURE, 0))
        target = max(self._attr_min_temp, min(temperature, self._attr_max_temp))

        await self.coordinator.async_set_control_value(self._temperature_target_key, target)
        self._device_status[self._temperature_target_key] = target
        self._handle_coordinator_update()
