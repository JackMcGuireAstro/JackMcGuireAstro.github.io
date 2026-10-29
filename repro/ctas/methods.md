# Methods

- Model: F_b(t) = A_b exp(-(t-t0)/tau_fall) / (1 + exp(-(t-t0)/tau_rise)) (flux (microjansky, AB zero point 23.9)); timing (and the plateau slope s) shared across bands; one amplitude per band.
- Fitter at release: scipy.optimize.least_squares (trf), deterministic starts.
- Input rule: ZTF g/r/i difference-PSF detections (ALeRCE, Fink, Lasair; one row per exposure) and ATLAS o/c forced-PSF detections, AB, uncertainty <= 0.5 mag.
- Reference: Bazin et al. 2009, A&A 499, 653 (2009A&A...499..653B).
- Claim boundary: A phenomenological fit to public difference and forced photometry; it describes the light-curve shape and is not a classification, a physical model or a distance.
