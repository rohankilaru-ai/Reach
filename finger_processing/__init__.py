"""Finger processing: camera -> state (curled/extended) and position of every finger.

    from finger_processing import finger_stream

    for frame in finger_stream():                 # continuous loop, one Frame per camera frame
        if frame.hand_present:
            for finger in frame.fingers.values():
                finger.name, finger.state, finger.tip, finger.tip_screen

Run `python -m finger_processing` for the live preview.
"""

from finger_processing.processing import (
    FINGERS,
    Calibration,
    Config,
    Finger,
    FingerPipeline,
    FingerProcessor,
    Frame,
    finger_stream,
    load_config,
    replay,
)

__all__ = [
    "FINGERS", "Calibration", "Config", "Finger", "FingerPipeline", "FingerProcessor",
    "Frame", "finger_stream", "load_config", "replay",
]
