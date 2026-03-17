import sys
import threading
import time


class LoadingSpinner:
    def __init__(self, message="Processing"):
        self.message = message
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._animate)

    def _animate(self):
        chars = ["-", "\\", "|", "/"]
        idx = 0
        while not self.stop_event.is_set():
            sys.stdout.write(f"\r{chars[idx % len(chars)]} {self.message}...")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.1)
        sys.stdout.write("\r" + " " * (len(self.message) + 25) + "\r")
        sys.stdout.flush()

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join()
