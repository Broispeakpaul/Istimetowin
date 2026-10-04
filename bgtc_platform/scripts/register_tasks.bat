@echo off
REM Registers the daily runs in Windows Task Scheduler. The PC clock is assumed to be Hong Kong time.
REM Re-running this file updates the tasks (/F). Remove them with: schtasks /Delete /TN "BGTC\*" /F
REM
REM Final runs (after the close, used for the morning report and exits):
REM   asia    18:20 HKT Mon-Fri  (HK 16:00, Tokyo/Seoul 14:30, India 18:00 HKT)
REM   europe  00:50 HKT Tue-Sat  (London 23:30 HKT until 25 Oct, 00:30 HKT after)
REM   us      06:00 HKT Tue-Sat  (New York 04:00 HKT until 1 Nov, 05:00 HKT after)
REM Preliminary runs (for "buy near the close"): the task starts early and the CLI waits until 30 minutes
REM before the real close, so the US and European clock changes need no edits here.
set HERE=%~dp0
schtasks /Create /F /TN "BGTC\asia_final"      /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:20 /TR "\"%HERE%run_session.bat\" asia"
schtasks /Create /F /TN "BGTC\europe_final"    /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 00:50 /TR "\"%HERE%run_session.bat\" europe"
schtasks /Create /F /TN "BGTC\us_final"        /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 06:00 /TR "\"%HERE%run_session.bat\" us"
schtasks /Create /F /TN "BGTC\asia_preclose"   /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:00 /TR "\"%HERE%run_session.bat\" asia --before-close 30"
schtasks /Create /F /TN "BGTC\europe_preclose" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 22:30 /TR "\"%HERE%run_session.bat\" europe --before-close 30"
schtasks /Create /F /TN "BGTC\us_preclose"     /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 03:00 /TR "\"%HERE%run_session.bat\" us --before-close 30"
echo Done. Check them in Task Scheduler under the BGTC folder.
