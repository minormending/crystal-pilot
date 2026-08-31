"""crystal-pilot: an auto-pilot for grinding in Pokemon Crystal."""
import warnings

# pysdl2-dll announces which SDL2 binaries it found, on import, every run. It is
# not actionable and it is the first thing you see, so it is filtered here --
# before anything imports PyBoy, which is what pulls SDL in.
warnings.filterwarnings(
    "ignore", message=r".*SDL2 binaries from pysdl2-dll.*"
)
