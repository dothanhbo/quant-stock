from abc import ABC, abstractmethod


class BaseStrategy(ABC):

    @abstractmethod
    def check_signal(
        self,
        symbol,
        reference_date=None,
        end_date=None,
    ):
        """
        Trả về:

        None
        hoặc dict signal
        """
        pass