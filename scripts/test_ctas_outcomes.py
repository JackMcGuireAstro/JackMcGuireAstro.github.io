#!/usr/bin/env python3
"""Guard against treating forced measurements or limits as detections."""
from export_ctas_snapshot import photometry_outcomes
rows = [
    {'detection': True},
    {'detection': False, 'limiting_flux': 1, 'photometry_method': 'forced'},
    {'detection': True, 'superseded': True},
    {'detection': True, 'retracted': True},
    {'flux': -1, 'pipeline': 'forced'},
    {'detection': False, 'limiting_flux': 'NaN'},
]
assert photometry_outcomes(rows) == {'active': 4, 'detections': 1, 'limits': 1, 'forced': 2}
assert photometry_outcomes([]) == {'active': 0, 'detections': 0, 'limits': 0, 'forced': 0}
print('CTAS reported outcome types and revision exclusions passed.')
