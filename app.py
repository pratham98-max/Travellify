from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from pymongo import MongoClient
from bson import ObjectId
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ---------------- DATABASE SETUP ---------------- #
client = MongoClient("mongodb://localhost:27017/")
db = client['travel_db']
destinations = db['destinations']
bookings = db['bookings']
reviews = db['reviews']
users = db['users']

# ---------------- LOGIN MANAGER SETUP ---------------- #
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

bcrypt = Bcrypt(app)

# ---------------- USER MODEL ---------------- #
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.email = user_data['email']

@login_manager.user_loader
def load_user(user_id):
    user_data = users.find_one({'_id': ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None


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
    return render_template('index.htm', destinations=all_destinations, query=query)


# ----------- SIGNUP ----------- #
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']

        if users.find_one({'email': email}):
            flash("Email already exists!", "danger")
            return redirect(url_for('signup'))

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        users.insert_one({'username': username, 'email': email, 'password': hashed_pw})
        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for('login'))
    return render_template('signup.htm')


# ----------- LOGIN ----------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user_data = users.find_one({'email': email})
        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            user = User(user_data)
            login_user(user)
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password", "danger")
    return render_template('login.htm')


# ----------- LOGOUT ----------- #
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "info")
    return redirect(url_for('login'))


# ----------- BOOKING ----------- #
@app.route('/book/<string:hotel_name>', methods=['GET', 'POST'])
@login_required
def book(hotel_name):
    hotel = destinations.find_one({'name': hotel_name})
    if not hotel:
        flash("Hotel not found!", "danger")
        return redirect(url_for('home'))

    if request.method == 'POST':
        name = request.form['name']
        checkin = request.form['checkin']
        checkout = request.form['checkout']

        bookings.insert_one({
            "user_id": current_user.id,
            "name": name,
            "hotel": hotel_name,
            "checkin": checkin,
            "checkout": checkout,
            "price": hotel.get("price", 0)
        })
        flash("Booking successful!", "success")
        return redirect(url_for('dashboard'))
    return render_template('booking.htm', hotel=hotel)


# ----------- REVIEWS (per hotel) ----------- #
@app.route('/review/<hotel_name>', methods=['GET', 'POST'])
@login_required
def review(hotel_name):
    hotel = destinations.find_one({'name': hotel_name})
    if not hotel:
        flash("Hotel not found!", "danger")
        return redirect(url_for('home'))

    if request.method == 'POST':
        rating = request.form['rating']
        comment = request.form['comment']
        reviews.insert_one({
            'user_id': current_user.id,
            'username': current_user.username,
            'hotel': hotel_name,
            'rating': int(rating),
            'comment': comment
        })
        flash("Review added successfully!", "success")
        return redirect(url_for('review', hotel_name=hotel_name))

    hotel_reviews = list(reviews.find({'hotel': hotel_name}))
    return render_template('review.htm', hotel=hotel, reviews=hotel_reviews)


# ----------- REVIEW LIST (all hotels) ----------- #
@app.route('/reviews')
@login_required
def review_list():
    all_hotels = list(destinations.find())
    return render_template('review_list.htm', destinations=all_hotels)


# ----------- DASHBOARD ----------- #
@app.route('/dashboard')
@login_required
def dashboard():
    user_bookings = list(bookings.find({'user_id': current_user.id}))
    user_reviews = list(reviews.find({'user_id': current_user.id}))
    return render_template('dashboard.htm', bookings=user_bookings, reviews=user_reviews, user=current_user)


if __name__ == '__main__':
    app.run(debug=True)
