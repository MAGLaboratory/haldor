# confirmation threshold written by brandon
class confirmation_threshold:
    # holdoff is the state where the input to the machine is different from
    # the stored value that the state machine is outputting
    holdoff = False
    def __init__(self, initial, delay):
        self.delay = delay
        self.confirmed = initial

    # returns true if the confirmed value changed
    def update(self, newValue, delay=0):
        if delay != 0:
            self.delay = delay
        if self.holdoff:
            if newValue == self.confirmed:
                self.holdoff = False;
                return (False, self.confirmed)
            elif self.time >= self.delay:
                self.confirmed = newValue
                self.holdoff = False
                return (True, self.confirmed)
            else:
                self.time += 1
                return (False, self.confirmed)
        else:
            if newValue != self.confirmed:
                # two because it is true this cycle
                # and it has to be true one more cycle to trigger the
                # condition in "if self.holdoff"::
                # "elif self.time >= self.delay"
                self.time = 2
                self.holdoff = True
                return (False, self.confirmed)
            else:
                return (False, self.confirmed)
            
