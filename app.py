from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO
import sqlite3

app = Flask(__name__)
socketio = SocketIO(app)

# 1. تهيئة قاعدة البيانات SQLite
def init_db():
    conn = sqlite3.connect('kejue.db')
    c = conn.cursor()
    # جدول المكالمات المستخرجة
    c.execute('''CREATE TABLE IF NOT EXISTS calls 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, company TEXT, use_case TEXT, summary TEXT, status TEXT)''')
    # جدول المواعيد المحجوزة
    c.execute('''CREATE TABLE IF NOT EXISTS booked_times 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, time_slot TEXT)''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

# 2. إرسال البيانات المحفوظة للواجهة عند عمل Refresh
@app.route('/get_all_calls', methods=['GET'])
def get_all_calls():
    conn = sqlite3.connect('kejue.db')
    c = conn.cursor()
    c.execute("SELECT name, company, use_case, summary, status FROM calls ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    
    calls = []
    for row in rows:
        calls.append({
            "prospect_name": row[0],
            "company_name": row[1],
            "use_case": row[2],
            "call_summary": row[3],
            "is_meeting_booked": True if row[4] == 'Meeting Booked' else False
        })
    return jsonify(calls)

# 3. استلام المكالمة من Sonus وحفظها
# 3. استلام المكالمة من Sonus وحفظها
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}
    
    # محاولة استخراج البيانات، وإذا كانت null نجعلها قاموساً فارغاً {}
    payload = {}
    if 'call' in data and 'extractions' in data['call'] and len(data['call']['extractions']) > 0:
        payload = data['call']['extractions'][0].get('result') or {}

    pName = payload.get('prospect_name', 'Unknown Prospect')
    cName = payload.get('company_name', 'Unknown Company')
    uCase = payload.get('use_case', 'N/A')
    summary = payload.get('call_summary', 'No summary available.')
    is_booked = payload.get('is_meeting_booked', False)
    status = 'Meeting Booked' if str(is_booked).lower() == 'true' else 'Call Completed'

    # حفظ في الداتا بيس
    conn = sqlite3.connect('kejue.db')
    c = conn.cursor()
    c.execute("INSERT INTO calls (name, company, use_case, summary, status) VALUES (?, ?, ?, ?, ?)",
              (pName, cName, uCase, summary, status))
    conn.commit()
    conn.close()

    socketio.emit('new_call', payload)
    return 'OK', 200

# 4. أداة للوكيل الذكي: فحص المواعيد المتاحة
@app.route('/check_availability', methods=['GET', 'POST'])
def check_availability():
    all_slots = ["10:00 AM","11:00 AM","12:00 PM","01:00 PM","02:00 PM","03:00 PM","04:00 PM","05:00 PM"]
    
    conn = sqlite3.connect('kejue.db')
    c = conn.cursor()
    c.execute("SELECT time_slot FROM booked_times")
    booked = [row[0] for row in c.fetchall()]
    conn.close()

    available = [slot for slot in all_slots if slot not in booked]
    
    if not available:
        return jsonify({"available_slots": "No slots available today. Propose tomorrow."}), 200
        
    return jsonify({
        "instruction": "Tell the user these are the available slots and ask which one they prefer.",
        "available_slots": available
    }), 200

# 5. أداة للوكيل الذكي: حجز الموعد
@app.route('/book_time', methods=['GET', 'POST'])
def book_time():
    data = request.json
    time_slot = data.get('time_slot')
    
    conn = sqlite3.connect('kejue.db')
    c = conn.cursor()
    c.execute("INSERT INTO booked_times (time_slot) VALUES (?)", (time_slot,))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "Success", "message": f"Time {time_slot} booked. Confirm with the user."}), 200

if __name__ == '__main__':
    socketio.run(app, port=5001, debug=True)
