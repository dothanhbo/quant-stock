from enum import Enum


class ExitExecution(Enum):
    NORMAL = "Normal"

    STOP_GAP = "Stop Gap"
    TARGET_GAP = "Target Gap"

    SAME_DAY_SL_FIRST = "Same Day SL First"