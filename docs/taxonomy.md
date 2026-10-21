# Ekchain Anomaly Taxonomy

**Important:** This taxonomy defines Ekchain's own anomaly codes for climate facility emissions auditing. It is **NOT** an official United Nations or IPCC taxonomy.

Each code includes an identifier, name, definition, how it's detected, and benign explanations that analysts should evaluate before concluding an anomaly.

## Code Definitions

### EKC-001: Emissions-fuel decoupling
- **Definition:** CO2 per heat input deviates from the unit's own baseline
- **Detection:** Comparison of measured CO2 emissions against the unit's baseline using hourly CEMS data and regression analysis
- **Benign explanations:**
  - Fuel switching during normal operation (e.g., natural gas to fuel oil) without changing heat input
  - Load profile changes causing variable heat input
  - Maintenance activities affecting sensor calibration or measurement
  - Thermal drift in sensors due to aging or environmental conditions
  - Correction factor adjustments during data quality flagging

### EKC-002: Flatlined or stuck values
- **Definition:** CO2 readings remain constant over extended periods despite operational changes
- **Detection:** Statistical detection of lack of variation: standard deviation below threshold, runs test for randomness, or sustained identical values over multiple hours
- **Benign explanations:**
  - Measurement system failure or sensor malfunction
  - Unit was offline or in shutdown mode
  - Corrective maintenance (e.g., sensor reset, cleaning)
  - Data logging interruption or system reboot
  - Use of substitute values from neighboring units or backup systems

### EKC-003: Emissions reported while unit was not operating
- **Definition:** CO2 values appear in the dataset for hours when the unit's opTime indicates no operation
- **Detection:** Cross-reference of CO2 measurements against operational timeline (opTime) derived from heat input and generator status, flagged when emissions appear without operation
- **Benign explanations:**
  - Off-hour testing, commissioning, or plant startup procedures
  - Data import from neighboring units or regional databases
  - Unit in standby or spinning reserve mode
  - System calibration activities with fuel input but no actual generation
  - Utility load balancing or economic dispatch situations where units are 'on' but not generating

### EKC-004: Abrupt unexplained step change
- **Definition:** Sudden jump or drop in CO2 values without documented operational events
- **Detection:** Time-series analysis detecting discontinuities using statistical process control methods or manual review of incident logs
- **Benign explanations:**
  - Fuel switching event (documented in shift logs or operational plan)
  - Sudden load increase from market dispatch signals
  - Generator startup or shutdown ramp-up with associated thermal effects
  - Sensor sensitivity change due to environmental factors (temperature, humidity)
  - Measurement system bias correction or recalibration

### EKC-005: Reporting gap (missing hours)
- **Definition:** Missing hourly CO2 measurements in the dataset due to telemetry loss or data transmission issues
- **Detection:** Gap detection algorithms based on sequence analysis of timestamps or expected reporting schedule
- **Benign explanations:**
  - Planned maintenance affecting telemetry infrastructure
  - Network outage or communication failure during a storm event
  - Data storage or backup system recovery
  - Scheduled meter reading cycles (e.g., hourly instead of 5-minute)

### EKC-006: Heavy reliance on substituted or calculated CO2 values
- **Definition:** Dataset contains predominantly calculated or substituted CO2 values rather than measured ones
- **Detection:** Analysis of value flags (measured vs. calculated vs. substitute) showing majority non-measured values
- **Benign explanations:**
  - Regulatory reporting requiring filling of missing values with estimates
  - Use of emission factors to compute CO2 from heat input and fuel composition
  - Data interpolation or regression imputation to fill gaps
  - Remote sensing or proxy measurements due to unavailable onsite monitors

### EKC-007: Cross-source mismatch
- **Definition:** CO2 values from different monitoring systems (continuous, batch, lab) disagree significantly
- **Detection:** Inter-method comparison and statistical tests (e.g., paired t-test) showing significant differences beyond measurement uncertainty
- **Benign explanations:**
  - Different measurement protocols or standards across systems
  - Temporal offset between data collection times
  - Changes in fuel composition or calorific value
  - System-specific correction factors not uniformly applied

### EKC-008: Explained operational event
- **Definition:** CO2 behavior matches a documented outage, startup, or shutdown event
- **Detection:** Correlation of emission patterns with recorded operational events (opTime = 0, startup/shutdown ramps, maintenance events)
- **Benign explanations:**
  - Planned unit outage (maintenance, repair)
  - Scheduled startup/shutdown during unit commissioning or test
  - System reset or calibration procedure
  - Planned operational mode change (e.g., unit cycling for grid balancing)

## Usage

- Use these codes in findings via the `mitre_attack_ids` field for structured anomaly categorization
- When a finding is determined to be EKC-006 (heavy reliance on substituted values), the conclusion must be "inconclusive"
- All findings should be described as "anomalies for review" rather than claims of fraud or intent
- Never claim that an EKC code proves compliance or violation of any regulatory standard
- The taxonomy list should be included in analyst and planner prompts when analyzing climate facility data