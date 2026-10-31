' Runs healthcheck.ps1 without opening a window (Programador de tareas, cada 5 min).
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\PandaBearAgent"
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\PandaBearAgent\healthcheck.ps1", 0, False
