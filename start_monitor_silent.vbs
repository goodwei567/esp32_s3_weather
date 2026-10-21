' Launch the collector and local web UI silently (no console window).
' Log output is written to pc\monitor.log
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = base & "\pc"
sh.Run "cmd /c set PYTHONUTF8=1&& python app.py > monitor.log 2>&1", 0, False
