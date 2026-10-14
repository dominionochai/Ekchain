# Test Fixtures for Climate Tools

These CSV files are **test fixtures only** - they contain synthetic data for testing
the climate forensics tools. They do NOT represent real EPA CEMS data.

## Files

- `stable.csv` - Normal operations, stable co2/heat ratio
- `co2_drop_flat_heat.csv` - 40% co2Mass drop while heatInput stays flat
- `shutdown_12h.csv` - 12-hour unit shutdown (OFFLINE)
- `with_gaps.csv` - Data with missing hours

All files use the standard CEMS hourly columns:
facilityId, facilityName, unitId, date, hour, grossLoad, heatInput, co2Mass, so2Mass, noxMass