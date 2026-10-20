# Mute the test game's audio (the Windows volume mixer's per-app mute) from outside the game.
#   powershell -ExecutionPolicy Bypass -File tools/test/mute_game.ps1 [-Watch [-IdleMinutes N]] [-Pattern "PR2_*"]
#   powershell -ExecutionPolicy Bypass -File tools/test/mute_game.ps1 -Watch -Names PR2_mymod,PR2_target
# Mutes every audio session whose process name matches -Pattern (default PR2_*: the test install's
# exes such as PR2_target.exe; the Steam PR2.exe does not match and is never touched). -Names gives
# exact process names instead (without .exe); only a name given there explicitly can be PR2, for a
# test that has to start the game through Steam. -Watch keeps doing it every 3 s, so games started
# later are muted too; with -IdleMinutes it stops after that long without a matching game running
# (tools/test/session.py starts it that way).
param([switch]$Watch, [int]$IdleMinutes = 0, [string]$Pattern = "PR2_*", [string[]]$Names = @())

if ($Pattern -ieq "PR2") { throw "refusing to match the Steam PR2.exe by pattern (name it in -Names to mean it)" }
$explicit = $Names.Count -gt 0

Add-Type -Language CSharp @"
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int flow, int mask, out IntPtr devices);
    int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice device);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
}
[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 {
    int GetAudioSessionControl(IntPtr g, int f, out IntPtr c);
    int GetSimpleAudioVolume(IntPtr g, int f, out IntPtr v);
    int GetSessionEnumerator(out IAudioSessionEnumerator e);
}
[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator { int GetCount(out int n); int GetSession(int i, out IAudioSessionControl2 s); }
[Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 {
    int GetState(out int s); int GetDisplayName(out IntPtr n); int SetDisplayName(IntPtr n, IntPtr g);
    int GetIconPath(out IntPtr p); int SetIconPath(IntPtr p, IntPtr g); int GetGroupingParam(out Guid g);
    int SetGroupingParam(IntPtr g, IntPtr c); int RegisterAudioSessionNotification(IntPtr n);
    int UnregisterAudioSessionNotification(IntPtr n); int GetSessionIdentifier(out IntPtr s);
    int GetSessionInstanceIdentifier(out IntPtr s); int GetProcessId(out uint pid);
}
[Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume {
    int SetMasterVolume(float v, IntPtr c); int GetMasterVolume(out float v);
    int SetMute(bool m, IntPtr c); int GetMute(out bool m);
}
public static class GameMute {
    public static int Run(string[] names, bool explicitNames) {
        var en = (IMMDeviceEnumerator)new MMDeviceEnumerator();
        IMMDevice dev; en.GetDefaultAudioEndpoint(0, 1, out dev);
        Guid iid = typeof(IAudioSessionManager2).GUID; object o;
        dev.Activate(ref iid, 23, IntPtr.Zero, out o);
        IAudioSessionEnumerator se; ((IAudioSessionManager2)o).GetSessionEnumerator(out se);
        int n; se.GetCount(out n); int muted = 0;
        for (int i = 0; i < n; i++) {
            IAudioSessionControl2 c; se.GetSession(i, out c); uint pid; c.GetProcessId(out pid);
            string name = "";
            try { name = Process.GetProcessById((int)pid).ProcessName; } catch { continue; }
            if (Array.IndexOf(names, name) < 0) continue;
            if (!explicitNames && name.Equals("PR2", StringComparison.OrdinalIgnoreCase)) continue;
            var v = (ISimpleAudioVolume)c; bool m; v.GetMute(out m);
            if (!m) { v.SetMute(true, IntPtr.Zero); muted++; }
        }
        return muted;
    }
}
"@

$seen = Get-Date
do {
    if ($explicit) {
        $names = @(Get-Process -Name $Names -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName } |
                   Sort-Object -Unique)
    } else {
        $names = @(Get-Process | Where-Object { $_.ProcessName -like $Pattern -and $_.ProcessName -ne "PR2" } |
                   ForEach-Object { $_.ProcessName } | Sort-Object -Unique)
    }
    $n = 0
    if ($names.Count -gt 0) {
        try { $n = [GameMute]::Run([string[]]$names, $explicit) } catch { $n = 0 }   # no audio device: try again later
        $seen = Get-Date
    }
    if ($n -gt 0) { "muted $n session(s) at $(Get-Date -Format HH:mm:ss)" }
    if ($names.Count -eq 0 -and $IdleMinutes -gt 0 -and ((Get-Date) - $seen).TotalMinutes -ge $IdleMinutes) { break }
    if ($Watch) { Start-Sleep -Seconds 3 }
} while ($Watch)
