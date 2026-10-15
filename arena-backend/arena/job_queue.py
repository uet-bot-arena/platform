"""Bounded queue: official games first, FIFO within each class."""
import itertools
import queue


class JobQueue(queue.PriorityQueue):
    def __init__(self, maxsize=16):
        super().__init__(maxsize=maxsize)
        self.sequence = itertools.count()

    def put(self, ident, block=True, timeout=None, *, official=False):
        return super().put((0 if official else 1, ident), block, timeout)

    def _put(self, item):
        priority, ident = item
        super()._put((priority, next(self.sequence), ident))

    def _get(self):
        return super()._get()[2]

    def practice_full(self):
        # Producers share the store lock. Reserve one queue slot for the single
        # outstanding official fixture so practice cannot block its dispatch.
        with self.mutex:
            practice = sum(priority == 1 for priority, _, _ in self.queue)
            return practice >= self.maxsize - 1 or len(self.queue) >= self.maxsize

    def put_official(self, ident):
        self.put(ident, block=False, official=True)

    def counts(self):
        with self.mutex:
            official = sum(priority == 0 for priority, _, _ in self.queue)
            return {"official": official, "practice": len(self.queue) - official}
