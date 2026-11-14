"""Finger processing: camera -> state (curled/extended) and position of every finger, both hands.

    from finger_processing import finger_stream

    for frame in finger_stream():                 # continuous loop, one Frame per camera frame
        for side, hand in frame.hands.items():    # "left" / "right", whichever are in view
            for finger in hand.fingers.values():
                finger.name, finger.state, finger.tip, finger.tip_screen
        frame.fingers, frame.pointer_screen       # the primary hand: cursor and gestures

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
    Hand,
    finger_stream,
    load_config,
    replay,
)

__all__ = [
    "FINGERS", "Calibration", "Config", "Finger", "FingerPipeline", "FingerProcessor",
    "Frame", "Hand", "finger_stream", "load_config", "replay",
]
