# Test Fixtures for Climate Tools

These CSV files are **test fixtures only** - they contain synthetic data for testing
the climate forensics tools. They do NOT represent real EPA CEMS data.

## Files

- `stable.csv` - Normal operations, stable co2/heat ratio
- `co2_drop_flat_heat.csv` - 40% co2Mass drop while heatInput stays flat
- `shutdown_12h.csv` - 12-hour unit shutdown (OFFLINE)
- `with_gaps.csv` - Data with missing hours

All files use the standard CEMS hourly columns plus optional EPA columns:
facilityId, facilityName, unitId, date, hour, grossLoad, heatInput,
co2Mass, so2Mass, noxMass, stateCode, unitType, primaryFuelInfo,
secondaryFuelInfo, heatInputMeasureFlg, co2MassMeasureFlg,
so2MassMeasureFlg, noxMassMeasureFlg, opTime

## Files

- `cems_hourly_stable.csv` - Normal operations, stable co2/heat ratio (all Measured)
- `cems_hourly_co2_drop_flat_heat.csv` - 40% co2Mass drop while heatInput stays flat (Measured)
- `cems_hourly_shutdown_12h.csv` - 12-hour unit shutdown (opTime=0, all zeros)
- `cems_hourly_with_gaps.csv` - Data with missing hours (all Measured)
- `cems_hourly_calculated.csv` - All co2MassMeasureFlg=Calculated (inconclusive)
- `cems_hourly_mixed_flags.csv` - Mix of Measured/Calculated with 70%+ Measured (co2 drop in Measured)
- `cems_hourly_optime_zero.csv` - opTime=0 hours with non-zero load (forces OFFLINE)
- `cems_hourly_no_measure_flag.csv` - Basic columns only, no co2MassMeasureFlg column