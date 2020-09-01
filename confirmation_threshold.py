# confirmation threshold written by brandon
class confirmation_threshold:
    holdoff = False
    def __init__(self, initial, delay):
        self.delay = delay
        self.confirmed = initial

    # returns true if the confirmed value changed
    def update(self, nv, delay=0):
        if delay != 0:
            self.delay = delay
        if self.holdoff:
            if nv == self.confirmed:
                self.holdoff = False;
                return (False, self.confirmed)
            elif self.time >= self.delay:
                self.confirmed = nv
                self.holdoff = False
                return (True, self.confirmed)
            else:
                self.time += 1
                return (False, self.confirmed)
        else:
            if nv != self.confirmed:
                self.time = 2
                self.holdoff = True
                return (False, self.confirmed)
            else:
                return (False, self.confirmed)
            
