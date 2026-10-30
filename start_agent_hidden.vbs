Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\PandaBearAgent"
WshShell.Run "cmd /c C:\PandaBearAgent\run_agent_loop.bat", 0, False
