# Methods

- Ephemeris selection: Per planet, the period and mid-transit epoch come from one catalog row, never combined across rows or sources: NASA PSCompPars if it reports both; else the NASA Planetary Systems default parameter set; else the most recently published PS row; else the TOI, K2 or KOI row with the smallest mid-time uncertainty propagated to the release date. Rows a source marks as false positive are never used. A TOI row is attached to a NASA planet only when both name the same TIC and their periods agree within 0.5%. Uncertainties are the larger of the reported upper and lower errors; null means not reported.
- Propagation: linear ephemeris, uncertainty added in quadrature, 1 sigma.
- Time standard: BJD_TDB.
