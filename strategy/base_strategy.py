from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):

    @abstractmethod
    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        """Đánh giá dữ liệu đã được chuẩn bị đầy đủ."""
        raise NotImplementedError