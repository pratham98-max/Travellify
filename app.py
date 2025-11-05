from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from pymongo import MongoClient
from bson import ObjectId
from flask_bcrypt import Bcrypt
import random, string
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO
import google.generativeai as genai

genai.configure(api_key="AIzaSyCIIDT0DAjDSMD9fvGscmTVvoOVK85QVnw")
model = genai.GenerativeModel("gemini-1.5-flash")


app = Flask(__name__)
app.secret_key = "supersecretkey"

# ---------------- DATABASE ---------------- #
client = MongoClient("mongodb://localhost:27017/")
db = client['travel_db']
destinations = db['destinations']
bookings = db['bookings']
reviews = db['reviews']
users = db['users']
payments = db['payments']

# ---------------- AUTH ---------------- #
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

bcrypt = Bcrypt(app)

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.email = user_data['email']
        self.role = user_data.get('role', 'user')

@login_manager.user_loader
def load_user(user_id):
    data = users.find_one({"_id": ObjectId(user_id)})
    return User(data) if data else None

# ---------------- HELPERS ---------------- #
def make_order_id(prefix="ORD"):
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    rand = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{prefix}-{ts}-{rand}"

# ---------------- ROUTES ---------------- #
@app.route('/')
def welcome():
    return render_template('welcome.htm')

@app.route('/home')
def home():
    query = request.args.get('search')
    if query:
        all_destinations = list(destinations.find({
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"location": {"$regex": query, "$options": "i"}}
            ]
        }))
    else:
        all_destinations = list(destinations.find())

    # attach avg rating + count for cards
    for h in all_destinations:
        hotel_reviews = list(reviews.find({"hotel": h["name"]}))
        if hotel_reviews:
            avg = sum(r["rating"] for r in hotel_reviews) / len(hotel_reviews)
            h["avg_rating"] = round(avg, 1)
            h["review_count"] = len(hotel_reviews)
        else:
            h["avg_rating"] = None
            h["review_count"] = 0

    return render_template('index.htm', destinations=all_destinations, query=query)

# ---------- AUTH ----------
@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        if users.find_one({'email': email}):
            flash("Email already exists!", "danger")
            return redirect(url_for('signup'))

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        users.insert_one({
            'username': username,
            'email': email,
            'password': hashed_pw,
            'role': 'user'
        })
        flash("Account created! Please log in.", "success")
        return redirect(url_for('login'))
    return render_template('signup.htm')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user_data = users.find_one({'email': email})
        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            login_user(User(user_data))
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        flash("Invalid email or password", "danger")
    return render_template('login.htm')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for('login'))

# ---------- BOOKING ----------
@app.route('/book/<string:hotel_name>', methods=['GET', 'POST'])
@login_required
def book(hotel_name):
    hotel = destinations.find_one({'name': hotel_name})
    if not hotel:
        flash("Hotel not found!", "danger")
        return redirect(url_for('home'))

    # room types support (fallback to single “Standard” using hotel.price)
    room_types = hotel.get("room_types") or [{"name": "Standard", "price": int(hotel.get("price", 0))}]

    # optional preselect room from query param
    preselected_room = request.args.get('room')

    if request.method == 'POST':
        name = request.form['name'].strip()

        # nights
        try:
            nights = int(request.form.get('nights', '1'))
        except ValueError:
            nights = 1
        if nights < 1:
            flash("Nights must be at least 1.", "danger")
            return redirect(url_for('book', hotel_name=hotel_name))

        # room type chosen
        chosen_room = request.form.get('room_type') or preselected_room or room_types[0]['name']
        rt = next((r for r in room_types if r['name'] == chosen_room), room_types[0])
        price_per_night = int(rt.get("price", hotel.get("price", 0)))
        total_price = price_per_night * nights

        checkin = request.form.get('checkin')  # optional
        checkout = None

        bookings.insert_one({
            "user_id": current_user.id,
            "name": name,
            "hotel": hotel_name,
            "room_type": chosen_room,
            "checkin": checkin,
            "checkout": checkout,
            "nights": nights,
            "price_per_night": price_per_night,
            "total_price": total_price,
            "paid": False,
            "txn_id": None
        })
        flash(f"Booking successful! {chosen_room} · {nights} night(s)", "success")
        return redirect(url_for('dashboard'))

    return render_template('booking.htm', hotel=hotel, room_types=room_types, preselected_room=preselected_room)


# ---------- HOTEL DETAILS (public) ----------
@app.route('/hotel/<hotel_name>')
def hotel_details(hotel_name):
    hotel = destinations.find_one({"name": hotel_name})
    if not hotel:
        flash("Hotel not found!", "danger")
        return redirect(url_for("home"))

    # images: support list in field "images", fallback to single "image"
    images = hotel.get("images")
    if not images:
        img = hotel.get("image")
        images = [img] if img else []

    # room types: fallback to single item from price
    room_types = hotel.get("room_types") or [{"name": "Standard", "price": int(hotel.get("price", 0))}]

    hotel_reviews = list(reviews.find({"hotel": hotel_name}))
    avg_rating = round(sum(r["rating"] for r in hotel_reviews) / len(hotel_reviews), 1) if hotel_reviews else None

    # simple similar suggestions (same location or any)
    similar = list(destinations.find({"location": hotel["location"], "name": {"$ne": hotel_name}}).limit(6))
    if len(similar) < 3:
        similar = list(destinations.find({"name": {"$ne": hotel_name}}).limit(6))

    return render_template("hotel_details.htm",
                           hotel=hotel,
                           images=images,
                           room_types=room_types,
                           reviews=hotel_reviews,
                           avg_rating=avg_rating,
                           similar=similar)


# ---------- PAYMENT ----------
@app.route("/payment/<booking_id>")
@login_required
def payment(booking_id):
    booking = bookings.find_one({"_id": ObjectId(booking_id), "user_id": current_user.id})
    if not booking:
        flash("Invalid booking!", "danger")
        return redirect(url_for("dashboard"))

    amount = booking.get("total_price", booking.get("price_per_night", 0))
    nights = booking.get("nights", 1)
    desc = f"{booking['hotel']} — {nights} night(s)"

    order_id = make_order_id()
    session["payment_data"] = {
        "order_id": order_id,
        "booking_id": booking_id,
        "amount": amount,
        "hotel": booking["hotel"],
        "nights": nights
    }
    return render_template("payment.htm", order_id=order_id, amount=amount, desc=desc)

@app.route("/payment/process", methods=["POST"])
@login_required
def process_payment():
    pay = session.get("payment_data")
    if not pay:
        flash("Session expired. Try again.", "danger")
        return redirect(url_for("dashboard"))

    txid = "TXN-" + ''.join(random.choices(string.digits, k=8))

    payments.insert_one({
        "order_id": pay["order_id"],
        "transaction_id": txid,
        "booking_id": pay["booking_id"],
        "amount": pay["amount"],  # total charged
        "hotel": pay["hotel"],
        "user": current_user.id,
        "timestamp": datetime.utcnow(),
        "status": "success"
    })

    bookings.update_one(
        {"_id": ObjectId(pay["booking_id"])},
        {"$set": {"paid": True, "txn_id": txid}}
    )

    session.pop("payment_data", None)

    return render_template("payment_result.htm",
                           order_id=pay["order_id"],
                           txid=txid,
                           amount=pay["amount"],
                           hotel=pay["hotel"])

# ---------- RECEIPT ----------
@app.route("/payment/receipt/<txn_id>")
@login_required
def download_receipt(txn_id):
    payment = payments.find_one({"transaction_id": txn_id, "user": current_user.id})
    if not payment:
        flash("Receipt not found.", "danger")
        return redirect(url_for("dashboard"))

    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(200, 800, "PAYMENT RECEIPT")

    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 760, f"Name: {current_user.username}")
    pdf.drawString(50, 740, f"Hotel: {payment['hotel']}")
    pdf.drawString(50, 720, f"Amount Paid: ₹{payment['amount']}")
    pdf.drawString(50, 700, f"Transaction ID: {payment['transaction_id']}")
    pdf.drawString(50, 680, f"Order ID: {payment['order_id']}")

    pdf.showPage()
    pdf.save()
    buf.seek(0)

    return send_file(buf, as_attachment=True,
                     download_name=f"receipt_{txn_id}.pdf",
                     mimetype='application/pdf')

# ---------- REVIEWS ----------
@app.route('/review/<hotel_name>', methods=['GET', 'POST'])
@login_required
def review(hotel_name):
    hotel = destinations.find_one({'name': hotel_name})
    if not hotel:
        flash("Hotel not found!", "danger")
        return redirect(url_for('home'))

    if request.method == 'POST':
        rating = int(request.form['rating'])
        comment = request.form['comment'].strip()
        reviews.insert_one({
            'user_id': current_user.id,
            'username': current_user.username,
            'hotel': hotel_name,
            'rating': rating,
            'comment': comment
        })
        flash("Review added!", "success")
        return redirect(url_for('review', hotel_name=hotel_name))

    hotel_reviews = list(reviews.find({'hotel': hotel_name}))
    return render_template('review.htm', hotel=hotel, reviews=hotel_reviews)

@app.route('/reviews')
@login_required
def review_list():
    all_hotels = list(destinations.find())
    return render_template('review_list.htm', destinations=all_hotels)

# ---------- USER DASHBOARD / PROFILE ----------
@app.route('/dashboard')
@login_required
def dashboard():
    user_bookings = list(bookings.find({'user_id': current_user.id}))
    user_reviews = list(reviews.find({'user_id': current_user.id}))
    return render_template('dashboard.htm', bookings=user_bookings, reviews=user_reviews)

@app.route('/profile')
@login_required
def profile():
    user_bookings = list(bookings.find({'user_id': current_user.id}))
    user_payments = list(payments.find({'user': current_user.id}))
    user_reviews = list(reviews.find({'user_id': current_user.id}))
    return render_template('profile.htm', user=current_user,
                           bookings=user_bookings, payments=user_payments, reviews=user_reviews)

# ---------- ADMIN ----------
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        flash("Access denied (admin only).", "danger")
        return redirect(url_for('dashboard'))
    return render_template('admin_dashboard.htm',
                           bookings=list(bookings.find()),
                           reviews=list(reviews.find()))


@app.route("/chatbot_api", methods=["POST"])
@login_required
def chatbot_api():
    msg = request.json.get("message", "")

    if not msg.strip():
        return jsonify({"reply": "Type something 😊"})

    try:
        response = model.generate_content(
            f"You are a helpful travel assistant. Keep answers short and friendly.\nUser: {msg}"
        )

        reply = response.text
        return jsonify({"reply": reply})

    except Exception as e:
        print("GEMINI ERROR:", e)
        return jsonify({"reply": "⚠️ Could not connect to Gemini. Check API key."})
    
@app.route("/chatbot")
@login_required
def chatbot_page():
    return render_template("chatbot.htm")




if __name__ == '__main__':
    app.run(debug=True)
