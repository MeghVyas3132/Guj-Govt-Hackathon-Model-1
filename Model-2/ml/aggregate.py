"""
ml.aggregate
============
Turns a stream of per-frame boxes into one record per vehicle.

The sandbox feeds run about 358 kbps at 1080p, which is roughly 0.006 bits per
pixel.  At that rate a single frame does not carry a readable plate, and no
amount of sharpening puts detail back that the encoder discarded.  What does
survive is *repetition*: compression noise differs from frame to frame while the
glyphs underneath do not, so thirty mediocre reads of one plate agree on the
truth more often than any single read does.

That is the whole reason this module exists.  It also happens to make the
pipeline much cheaper, because the expensive models (CLIP, the plate detector,
the OCR) then run a handful of times per *vehicle* instead of once per box per
frame — a vehicle in view for forty frames used to cost forty of each.

Ownership: one aggregator per camera, because ``track_id`` is only unique within
a single tracker instance.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger(__name__)


# ─── crop selection ───────────────────────────────────────────────────────────

def crop_quality(crop_bgr: np.ndarray, conf: float) -> float:
    """Score how worth-modelling one crop of a vehicle is.

    Combines size, focus and detector confidence.  Size dominates — a plate is
    legible or it is not, and that is mostly a question of pixels — but the
    Laplacian term breaks ties between similarly sized crops by rejecting the
    motion-blurred ones, which at 30 fps under sodium light is most of them.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0
    h, w = crop_bgr.shape[:2]
    if h < 8 or w < 8:
        return 0.0
    grey = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(grey, cv2.CV_64F).var())
    return float(np.sqrt(w * h) * np.log1p(sharpness) * max(conf, 0.0))


# ─── plate consensus ──────────────────────────────────────────────────────────

def vote_plate(
    reads: list[tuple[str, list[float] | None]],
) -> tuple[str | None, float, int]:
    """Fuse many noisy OCR reads of one plate into a single string.

    Args:
        reads: ``(text, char_probs)`` pairs.  ``char_probs`` may be None.

    Returns:
        ``(voted_text, agreement, n_votes)``.  *agreement* is the mean share of
        confidence mass the winning character took at each position — how much
        the reads agreed, not how confident any one of them was.  A plate voted
        by twenty reads that all disagree should not look trustworthy, and this
        is the number that says so.
    """
    reads = [(t, p) for t, p in reads if t]
    if not reads:
        return None, 0.0, 0

    # Reads of different lengths cannot be aligned position-by-position, so
    # settle on the most common length first and vote only within that group.
    modal_len = Counter(len(t) for t, _ in reads).most_common(1)[0][0]
    aligned = [(t, p) for t, p in reads if len(t) == modal_len]

    chars: list[str] = []
    agreements: list[float] = []
    for i in range(modal_len):
        weight: dict[str, float] = defaultdict(float)
        for text, probs in aligned:
            # A read with no per-character confidence still gets a vote, just a
            # middling one — dropping it would throw away real evidence.
            weight[text[i]] += probs[i] if probs and i < len(probs) else 0.5
        total = sum(weight.values())
        ch, score = max(weight.items(), key=lambda kv: kv[1])
        chars.append(ch)
        agreements.append(score / total if total else 0.0)

    return "".join(chars), float(np.mean(agreements)), len(aligned)


def finalise_plate(
    reads: list[tuple[str, list[float] | None]],
    min_agreement: float = 0.6,
) -> tuple[str | None, str | None, float, int]:
    """Vote, then validate, and say honestly which of those two produced the text.

    Returns ``(text, status, agreement, n_votes)`` where status is:

    ``exact``        the voted string is a well-formed Indian plate as read
    ``corrected``    it only became well-formed after character substitution
    ``unvalidated``  it never did

    Keeping these apart matters more here than it looks.  A registration that is
    well-formed but wrong is worse than no registration at all when the reader
    is a police officer, so only ``exact`` should reach an alert or a search.
    """
    from ml.plate_validator import _PLATE_PATTERN, INDIAN_STATE_CODES, validate_plate

    voted, agreement, n_votes = vote_plate(reads)
    if not voted:
        return None, None, 0.0, 0

    # Reads that disagree with each other can still vote to something
    # well-formed by accident. Shape alone is not evidence, so a plate the reads
    # did not actually agree on never earns `exact`, however valid it looks.
    if agreement < min_agreement:
        return voted, "unvalidated", agreement, n_votes

    match = _PLATE_PATTERN.match(voted)
    if match and match.group(1) in INDIAN_STATE_CODES:
        return voted, "exact", agreement, n_votes

    corrected = validate_plate(voted)
    if corrected:
        return corrected, "corrected", agreement, n_votes

    return voted, "unvalidated", agreement, n_votes


# ─── track state ──────────────────────────────────────────────────────────────

@dataclass
class BestCrop:
    quality: float
    image: np.ndarray
    bbox: dict
    archive_ms: float


@dataclass
class TrackState:
    """Everything seen of one vehicle, while it is still in view."""

    track_id: int
    camera_id: str
    first_ms: float
    last_ms: float
    last_frame: int
    n_observations: int = 0
    classes: Counter = field(default_factory=Counter)
    colours: Counter = field(default_factory=Counter)
    best: list[BestCrop] = field(default_factory=list)
    plate_reads: list[tuple[str, list[float] | None]] = field(default_factory=list)
    trajectory: list[tuple[float, float, float]] = field(default_factory=list)

    @property
    def class_name(self) -> str:
        return self.classes.most_common(1)[0][0] if self.classes else "unknown"

    @property
    def dominant_colour(self) -> str | None:
        """Modal colour, ignoring frames the bucketing gave up on."""
        named = Counter({k: v for k, v in self.colours.items() if k and k != "unknown"})
        return named.most_common(1)[0][0] if named else None

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.last_ms - self.first_ms)


# ─── aggregator ───────────────────────────────────────────────────────────────

class TrackAggregator:
    """Accumulates per-frame observations; emits one finished track at a time.

    A track is considered finished when it has not been seen for
    ``idle_frames`` processed frames.  That has to be longer than the tracker's
    own ``track_buffer``, or a vehicle briefly occluded by a bus gets closed and
    reopened as two separate vehicles.
    """

    def __init__(self, camera_id: str, keep_best: int = 3, idle_frames: int = 45) -> None:
        self.camera_id = camera_id
        self.keep_best = keep_best
        self.idle_frames = idle_frames
        self.tracks: dict[int, TrackState] = {}

    def add(
        self,
        track_id: int,
        class_name: str,
        crop_bgr: np.ndarray,
        bbox: dict,
        conf: float,
        archive_ms: float,
        frame_idx: int,
        colour: str | None = None,
    ) -> None:
        track = self.tracks.get(track_id)
        if track is None:
            track = self.tracks[track_id] = TrackState(
                track_id=track_id,
                camera_id=self.camera_id,
                first_ms=archive_ms,
                last_ms=archive_ms,
                last_frame=frame_idx,
            )

        track.last_ms = archive_ms
        track.last_frame = frame_idx
        track.n_observations += 1
        track.classes[class_name] += 1
        if colour:
            track.colours[colour] += 1
        track.trajectory.append(
            (archive_ms,
             (bbox["x1"] + bbox["x2"]) / 2.0,
             (bbox["y1"] + bbox["y2"]) / 2.0)
        )

        quality = crop_quality(crop_bgr, conf)
        if quality <= 0.0:
            return
        # Keep only the best few crops. copy() because the frame buffer this
        # slice views is reused by the decoder on the next read.
        track.best.append(BestCrop(quality, crop_bgr.copy(), dict(bbox), archive_ms))
        track.best.sort(key=lambda b: -b.quality)
        del track.best[self.keep_best:]

    def add_plate_read(
        self, track_id: int, text: str, char_probs: list[float] | None
    ) -> None:
        track = self.tracks.get(track_id)
        if track is not None and text:
            track.plate_reads.append((text, char_probs))

    def reap(self, frame_idx: int) -> Iterator[TrackState]:
        """Yield tracks that have gone quiet — vehicles that have left the scene."""
        finished = [
            tid for tid, t in self.tracks.items()
            if frame_idx - t.last_frame > self.idle_frames
        ]
        for tid in finished:
            yield self.tracks.pop(tid)

    def drain(self) -> Iterator[TrackState]:
        """Yield everything still open. For shutdown, and the end of an archive."""
        while self.tracks:
            yield self.tracks.popitem()[1]

    def __len__(self) -> int:
        return len(self.tracks)
