import sqlite3

conn = sqlite3.connect('db.sqlite3')
cur = conn.cursor()
with open('debug_db_output.txt', 'w', encoding='utf-8') as f:
    f.write('rows=' + str(cur.execute('select count(*) from api_evaluation').fetchone()[0]) + '\n')
    f.write('date-2026-10-14=' + str(cur.execute('select count(*) from api_evaluation where date="2026-10-14"').fetchone()[0]) + '\n')
    f.write('matching employee ids=' + str(cur.execute("select id,name from api_employee where lower(name) like '%arielle%' or lower(name) like '%cruz%' ").fetchall()) + '\n')
    f.write('matching eval rows=' + str(cur.execute("select e.id, emp.name, e.date, (e.productivity_score+e.attendance_score+e.quality_score+e.teamwork_score+e.communication_score)/5.0 as avg from api_evaluation e join api_employee emp on emp.id=e.employee_id where lower(emp.name) like '%arielle%' or lower(emp.name) like '%cruz%' order by e.date").fetchall()) + '\n')
    f.write('recent evals=' + str(cur.execute("select e.id, emp.name, e.date, (e.productivity_score+e.attendance_score+e.quality_score+e.teamwork_score+e.communication_score)/5.0 as avg from api_evaluation e join api_employee emp on emp.id=e.employee_id order by e.date desc, e.id desc limit 10").fetchall()) + '\n')
print('done')
