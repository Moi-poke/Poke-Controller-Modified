#!/usr/bin/env python3

from abc import ABC, abstractmethod


class Command(ABC):
    def __init__(self):
        self.isRunning = False

    @abstractmethod
    def start(self, ser, postProcess=None):
        pass

    @abstractmethod
    def end(self, ser):
        pass
