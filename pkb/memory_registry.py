"""Canonical predicates and deterministic effects; never supplied by a model."""
from dataclasses import dataclass


@dataclass(frozen=True)
class EffectRule:
    state_predicate: str
    entity_types: frozenset[str]
    value_key: str | None = None

    def select(self, value):
        return value[self.value_key] if self.value_key else value


COMPONENT_TYPES = frozenset({'gpu', 'network_adapter', 'rc_servo'})
EFFECT_RULES = {
    'driver_updated': EffectRule('current_driver', COMPONENT_TYPES),
    'servo_updated': EffectRule('current_servo', COMPONENT_TYPES),
    'os_release_changed': EffectRule('current_os_release', frozenset({'computer', 'pc'}), 'to_release'),
}
PREDICATE_REGISTRY = {
    'os.upgrade': 'os_release_changed',
    'driver.update': 'driver_updated',
    'servo.replace': 'servo_updated',
    'performance.slow': 'performance_observed',
    'user.hypothesis': 'user_hypothesis',
    **{p: p for p in ('driver_updated', 'servo_updated', 'os_release_changed',
                     'current_driver', 'current_servo', 'current_os_release',
                     'manufacturer', 'model', 'serial_number', 'purchase_date')},
}
