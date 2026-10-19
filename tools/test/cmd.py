"""Send a command to a running gamehost.py and print its reply (the new lines of its log).

    python tools/test/cmd.py click X Y                 left click at window pixel X, Y
    python tools/test/cmd.py move X Y | wheel X Y [DELTA] [TIMES]
    python tools/test/cmd.py key VK [TIMES]            a virtual-key code, e.g. 0x1B (Esc)
    python tools/test/cmd.py read 0x<addr> [n]
    python tools/test/cmd.py write 0x<addr> <hex>
    python tools/test/cmd.py bp 0xVA=reg,reg+off:len,...
    python tools/test/cmd.py call 0xVA THIS [ARG ...]  THIS: 0xADDR, 0 or menu; ARG: int or s:Text_With_Underscores
    python tools/test/cmd.py towns 0x<CStadtLager vtable>
    python tools/test/cmd.py setnation 0x<lager> NATION DISPLAY

Input is injected on the game's UI thread (gamehost.py): the real mouse and keyboard are not used.
"""
import sys
sys.stdout.reconfigure(errors="replace")
import time

from gamehost import CMD_FILE, RUNS

if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    print(__doc__)
    sys.exit(0)
done = CMD_FILE.with_suffix(".done")
done.unlink(missing_ok=True)
log = max(RUNS.glob("*/gamehost.log"))
start = log.stat().st_size
CMD_FILE.write_text(" ".join(sys.argv[1:]) + "\n")
for _ in range(600):
    if done.exists():
        break
    time.sleep(0.1)
else:
    sys.exit("no reply in 60s (is gamehost.py running?)")
with open(log, encoding="utf-8", errors="replace") as f:
    f.seek(start)
    print(f.read(), end="")
