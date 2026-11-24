"""Always-on-top Windows message box with a beep: popup.py "<title>" "<message>". Fallback when the GUI isn't open."""
import ctypes, sys, winsound
for _ in range(3):
    winsound.Beep(1200, 250)
ctypes.windll.user32.MessageBoxW(0, sys.argv[2], sys.argv[1], 0x30 | 0x40000 | 0x10000 | 0x1000)
