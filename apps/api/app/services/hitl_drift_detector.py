"""Per-bucket SPC sampling-rate driver (ADR-023 §6, EPIC-A A.3 part 2, #215).

The :class:`HITLDriftDetector` reads the
``samples_human_after_auto / samples_auto`` ratio from the
:class:`SamplingStateStore` and returns an effective per-bucket
sampling rate. When the ratio crosses
``KW_HITL_DRIFT_THRESHOLD`` for a bucket, the bucket's sampling rate
ramps up by ``KW_HITL_DRIFT_RAMP_FACTOR`` × baseline (capped at 1.0)
so the SPC sampler escalates more versions to humans for that
``(content_type, topic_cluster)`` combination — the canonical "model
quality is regressing for this slice of the corpus" response.

Design choices
--------------
- The detector is a pure read over the sampling store. Writes to
  ``samples_human_after_auto`` happen at the rejection-handler call
  site (see :class:`app.services.review_service.ReviewService`); the
  detector never mutates state.
- Cold-start (``samples_auto == 0``) returns the baseline. With no
  auto decisions yet, there's no denominator and the ratio is
  undefined — the safe posture is the configured baseline.
- Below the drift threshold, the rate stays at baseline. Above it,
  the rate ramps once (no continuous scaling) so the response is
  predictable: "this bucket is drifting, escalate ramp_factor× more
  often" is the simple knob ADR-023 §6 specifies.
- The ramp is a multiplicative cap, not a floor + offset, so a
  ``ramp_factor`` of 0 actually decreases the rate (corner case
  documented; defaults make this impossible). The cap to 1.0 means
  ``baseline=0.05, ramp_factor=100`` saturates at "escalate every
  version" rather than overshooting.

The router takes the detector via a callable (see
:class:`app.services.hitl_router.HITLRouter`) so tests can pin the
returned rate without wiring a full sampling store.
"""

from __future__ import annotations

import logging
import math

from app.services.sampling_state_store import SamplingBucket, SamplingStateStore

log = logging.getLogger(__name__)


class HITLDriftDetector:
    """Per-bucket SPC sampling-rate driver per ADR-023 §6.

    Reads ``samples_human_after_auto / samples_auto`` from the
    sampling_state store; when above ``drift_threshold`` for a
    bucket, the bucket's sampling rate ramps up by ``ramp_factor`` ×
    ``baseline_rate`` (capped at 1.0).

    Constructor parameters
    ----------------------
    sampling_state:
        The store the detector reads counters from. Same instance the
        router and the auto-promoter write to — that's how the drift
        signal flows from "human reviewer rejected an auto-eligible
        version" all the way back to "future router decisions on this
        bucket sample more aggressively".
    baseline_rate:
        The cold-start rate every bucket starts at, in [0.0, 1.0].
        Mirrors :data:`Settings.hitl_spc_sample_rate`; the wiring
        layer passes it through.
    drift_threshold:
        Ratio above which the bucket's sampling rate ramps. ADR-023
        §6 calls out 0.10 as the "10% of human reviews on
        auto-eligible versions are rejections" canonical value;
        operators may tune.
    ramp_factor:
        Multiplier applied to ``baseline_rate`` for drifting buckets.
        Default 10.0 takes the 0.05 baseline to 0.5 sampling for the
        bucket.
    """

    def __init__(
        self,
        *,
        sampling_state: SamplingStateStore,
        baseline_rate: float,
        drift_threshold: float,
        ramp_factor: float,
    ) -> None:
        if not 0.0 <= baseline_rate <= 1.0 or math.isnan(baseline_rate):
            raise ValueError(
                f"baseline_rate must lie in [0.0, 1.0]; got {baseline_rate!r}.",
            )
        if drift_threshold < 0.0 or math.isnan(drift_threshold):
            raise ValueError(
                f"drift_threshold must be non-negative; got {drift_threshold!r}.",
            )
        if ramp_factor < 0.0 or math.isnan(ramp_factor):
            raise ValueError(
                f"ramp_factor must be non-negative; got {ramp_factor!r}.",
            )
        self._sampling_state = sampling_state
        self._baseline_rate = baseline_rate
        self._drift_threshold = drift_threshold
        self._ramp_factor = ramp_factor

    @property
    def baseline_rate(self) -> float:
        """The cold-start sampling rate every bucket starts at."""
        return self._baseline_rate

    @property
    def drift_threshold(self) -> float:
        """The ratio above which a bucket ramps."""
        return self._drift_threshold

    @property
    def ramp_factor(self) -> float:
        """The multiplier applied to baseline_rate for drifting buckets."""
        return self._ramp_factor

    def sampling_rate(self, bucket: tuple[str, str]) -> float:
        """Return the effective per-bucket rate in [0.0, 1.0].

        Returns ``baseline_rate`` for cold-start (no auto samples
        yet) or buckets at/below the drift threshold; otherwise
        ``min(1.0, baseline_rate * ramp_factor)``.

        ``bucket`` is the 2-tuple ``(content_type, topic_cluster)``
        the router stamps on every :class:`RoutingDecision`. The
        sentinel ``"_unknown_"`` cluster reads back the same way as
        any other cluster — there's no special-casing here.
        """
        sampling_bucket = SamplingBucket(
            content_type=bucket[0],
            topic_cluster=bucket[1],
        )
        counters = self._sampling_state.read_counters(bucket=sampling_bucket)

        if counters.samples_auto == 0:
            # Cold-start: no auto decisions yet, ratio is undefined.
            # Hold at baseline.
            return self._baseline_rate

        ratio = counters.samples_human_after_auto / counters.samples_auto
        if ratio <= self._drift_threshold:
            return self._baseline_rate

        ramped = self._baseline_rate * self._ramp_factor
        return min(1.0, ramped)


__all__ = ["HITLDriftDetector"]
