"""Per-run seeded RNG. seed + counter persist on the run row so runs are replayable."""
import random


class RunRNG:
    def __init__(self, seed: int, counter: int):
        self.seed = seed
        self.counter = counter

    def next(self) -> random.Random:
        """A fresh Random derived from (seed, counter); increments the counter."""
        r = random.Random((self.seed << 20) ^ self.counter)
        self.counter += 1
        return r

    def randint(self, a: int, b: int) -> int:
        return self.next().randint(a, b)

    def random(self) -> float:
        return self.next().random()
