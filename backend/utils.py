import random
import time


def rand_delay(label: str = ""):
    """Small random delay to de-sync calls to GPT a bit."""
    d = random.uniform(1.5, 4.0)
    print(f"[delay] {label}: sleeping {d:.2f}s")
    time.sleep(d)
