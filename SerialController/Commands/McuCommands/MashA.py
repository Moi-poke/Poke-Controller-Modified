#!/usr/bin/env python3

from Commands.McuCommandBase import McuCommand


# Mash A button
class Mash_A(McuCommand):
    NAME = "A連打"

    def __init__(self, sync_name="mash_a"):
        super().__init__(sync_name)
