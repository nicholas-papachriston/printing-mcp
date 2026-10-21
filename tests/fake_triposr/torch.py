"""Stand-in for torch so the local mesh backend runs without a GPU stack."""

import contextlib


def manual_seed(seed):
    return seed


def no_grad():
    return contextlib.nullcontext()


class cuda:  # noqa: N801 - mirrors torch.cuda
    @staticmethod
    def is_available():
        return False
