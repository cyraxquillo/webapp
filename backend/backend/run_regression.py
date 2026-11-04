import subprocess

result = subprocess.run(
    [
        r"C:\Users\PC38\AppData\Local\Temp\lexie-backend-role-env\Scripts\python.exe",
        "manage.py",
        "test",
        "api.tests_attendance.AttendanceTimeInTests.test_default_automation_settings_are_recreated_without_overwriting_existing_data",
        "--verbosity",
        "1",
    ],
    cwd=r"C:\Users\PC38\Downloads\webapp 2\webapp 2\webapp\backend",
    capture_output=True,
    text=True,
)
print(result.stdout)
print(result.stderr)
print(f"EXIT_CODE={result.returncode}")
raise SystemExit(result.returncode)
