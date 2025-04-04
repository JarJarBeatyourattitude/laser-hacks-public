import json
import os
import random
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from email_validator import validate_email, EmailNotValidError
from openai import OpenAI
# from dotenv import load_dotenv

# load_dotenv()

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-please-change')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# --- Database Models ---
# (Models are unchanged from the previous correct version)
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    events = db.relationship('Event', backref='creator', lazy='dynamic', cascade="all, delete-orphan")
    commitments = db.relationship('Commitment', backref='user', lazy='dynamic', cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    subject = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(50), nullable=True)
    proposed_time = db.Column(db.String(100), nullable=False)
    proposed_location = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=True)
    reasoning = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='proposed', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    # Relationship uses default lazy loading (compatible with joinedload)
    commitments = db.relationship('Commitment', backref='event', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Event {self.id}: {self.subject}>'

class Commitment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False, index=True)
    committed_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'event_id', name='_user_event_uc'),)

    def __repr__(self):
        return f'<Commitment User:{self.user_id} to Event:{self.event_id}>'

# --- Flask-Login Setup ---
@login_manager.user_loader
def load_user(user_id):
    # FIXED: Use db.session.get() instead of User.query.get()
    # This addresses the LegacyAPIWarning
    return db.session.get(User, int(user_id))

# --- OpenAI LLM Interaction ---
# (No changes needed in this function)
def call_openai_llm(student_data, user_name="Student"):
    """Calls OpenAI API to generate event proposals."""
    print(f"--- Calling OpenAI API for user: {user_name} ---")
    print(f"Raw Input Data: {student_data}")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set.")
        raise ValueError("OpenAI API key not found in environment variables.")
    try:
        client = OpenAI(api_key=api_key)
    except Exception as e:
        print(f"ERROR: Failed to initialize OpenAI client: {e}")
        raise ValueError("Failed to initialize OpenAI client.")

    system_prompt = """
    You are an assistant for Irvine Valley College (IVC) students helping them organize study groups and social meetups.
    Analyze the user's desired meetup (classes, purpose, availability) and propose potential meetup events.
    Focus on the classes/purpose mentioned. Suggest specific times/locations suitable for IVC.

    You MUST output your response as a JSON list of objects. Each object represents one proposal and MUST have keys:
    "type", "subject", "details", "proposed_time", "proposed_location", "reasoning".
    Generate 1-2 relevant suggestions based on the input. Ensure the entire output is ONLY the JSON list.
    Keep details concise. The user initiating this is {user_name}. Reference them in the details.
    Example Input: {{"classes": "CIS 1A", "availability": "Tuesday afternoons", "purpose": "Work on Lab 3"}}
    Example JSON output:
    [
      {{
        "type": "Study Group",
        "subject": "CIS 1A Lab 3 Work Session",
        "details": "Proposed session for CIS 1A Lab 3, initiated by {user_name}.",
        "proposed_time": "Tuesday 2:00 PM",
        "proposed_location": "BSTIC Computer Lab",
        "reasoning": "Matches class, purpose, and availability. BSTIC lab is relevant for CIS."
      }}
    ]
    """.format(user_name=user_name)

    user_prompt = f"Here is the student's desired meetup information:\n{json.dumps(student_data)}"

    try:
        print("Sending request to OpenAI API...")
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={ "type": "json_object" }
        )
        print("Received response from OpenAI API.")
        json_string = response.choices[0].message.content
        print(f"Raw API Response Content:\n{json_string}")
        parsed_response = json.loads(json_string)

        if isinstance(parsed_response, list):
             petitions_list = parsed_response
        elif isinstance(parsed_response, dict) and len(parsed_response) > 0:
             potential_list = next((v for v in parsed_response.values() if isinstance(v, list)), None)
             if potential_list is not None:
                 petitions_list = potential_list
             else:
                 raise ValueError("JSON response is a dict, but no list found as a value.")
        else:
             raise ValueError("Unexpected JSON structure received from API.")

        valid_petitions = []
        required_keys = ["type", "subject", "details", "proposed_time", "proposed_location", "reasoning"]
        for item in petitions_list:
            if isinstance(item, dict) and all(key in item for key in required_keys):
                valid_petitions.append(item)
            else:
                print(f"Warning: Petition object missing expected keys or not a dict: {item}")

        print(f"--- OpenAI Call Successful. Valid Proposed Petitions: {json.dumps(valid_petitions, indent=2)} ---")
        return valid_petitions

    except Exception as e:
        print(f"ERROR: OpenAI API call or processing failed: {e}")
        raise

# --- Routes ---
# (Routes index, propose_event_form, commit_to_event, signup, login, logout are unchanged from the previous correct version,
#  except where noted by comments if any minor internal logic changes were needed)

@app.route('/')
def index():
    """Displays existing events. Requires login."""
    if not current_user.is_authenticated:
         flash('Please log in or sign up to view and propose events.', 'info')
         return redirect(url_for('login'))

    try:
        # Eager loading query (should work now)
        events = Event.query.options(
            db.joinedload(Event.creator),
            db.joinedload(Event.commitments).joinedload(Commitment.user)
        ).order_by(Event.created_at.desc()).all()
    except Exception as e:
        print(f"Error fetching events in index route: {e}")
        flash("Error loading events. Please try again later.", "error")
        events = []

    return render_template('index.html', events=events)

@app.route('/propose', methods=['GET', 'POST'])
@login_required
def propose_event_form():
    """Handles the form for users to describe their desired event, calls LLM, and saves proposals."""
    if request.method == 'POST':
        classes = request.form.get('classes', '').strip()
        availability = request.form.get('availability', '').strip()
        purpose = request.form.get('purpose', '').strip()

        if not availability or not purpose:
             flash('Please describe the purpose and your availability.', 'error')
             return render_template('propose_form.html', form_data=request.form)

        student_input_data = {
             "classes": classes,
             "availability": availability,
             "purpose": purpose
         }

        try:
            user_identifier = current_user.name if current_user.name else current_user.email
            proposed_events_data = call_openai_llm(student_input_data, user_identifier)

            if not proposed_events_data:
                flash('The AI could not generate any event suggestions based on your input. Try being more specific.', 'info')
                return render_template('propose_form.html', form_data=request.form)

            num_saved = 0
            for event_data in proposed_events_data:
                try:
                    new_event = Event(
                        user_id=current_user.id,
                        subject=event_data.get('subject'),
                        type=event_data.get('type'),
                        proposed_time=event_data.get('proposed_time'),
                        proposed_location=event_data.get('proposed_location'),
                        details=event_data.get('details'),
                        reasoning=event_data.get('reasoning'),
                        status='proposed'
                    )
                    db.session.add(new_event)
                    num_saved += 1
                except Exception as e:
                    print(f"Error creating event object from data {event_data}: {e}")
                    flash(f"Warning: Could not process one of the AI suggestions.", "warning")

            if num_saved > 0:
                db.session.commit()
                flash(f'{num_saved} new event proposal(s) created!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Could not save any AI suggestions. Please try again.', 'error')
                return render_template('propose_form.html', form_data=request.form)

        except ValueError as ve:
            flash(f'Configuration Error: {ve}', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected error occurred: {e}', 'error')
            print(f"Error in /propose POST route: {e}")

        return render_template('propose_form.html', form_data=request.form)

    # GET Request
    return render_template('propose_form.html')

@app.route('/commit/<int:event_id>', methods=['POST'])
@login_required
def commit_to_event(event_id):
    """Allows a logged-in user to commit to attending an event via AJAX POST request."""
    event = db.session.get(Event, event_id) # Use newer db.session.get()
    if not event:
        return jsonify({"error": "Event not found"}), 404

    existing_commitment = Commitment.query.filter_by(user_id=current_user.id, event_id=event_id).first()
    if existing_commitment:
        return jsonify({"error": "Already committed to this event"}), 409

    try:
        new_commitment = Commitment(user_id=current_user.id, event_id=event_id)
        db.session.add(new_commitment)
        db.session.commit()

        # Reminder Simulation
        user_contact = current_user.phone if current_user.phone else current_user.email
        print(f"--- REMINDER SIMULATION ---")
        print(f"User: {current_user.email} (ID: {current_user.id}) committed to Event: {event.subject} (ID: {event.id})")
        print(f"Would send reminder to contact: {user_contact}")
        print(f"--------------------------")

        # Pass back user name for dynamic update in JS
        return jsonify({"message": "Successfully committed", "user_name": current_user.name or current_user.email}), 201

    except Exception as e:
        db.session.rollback()
        print(f"Error committing User {current_user.id} to Event {event_id}: {e}")
        return jsonify({"error": "Database error during commitment. Please try again."}), 500


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        error = False
        if not email or not password or not confirm_password:
            flash('Please fill out all required fields (Email, Password, Confirm Password).', 'error'); error = True
        if password != confirm_password:
            flash('Passwords do not match.', 'error'); error = True
        try:
            valid_email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as e:
            flash(f"Invalid email address: {e}", 'error'); error = True; valid_email = None
        # Check if user exists using filter_by (more reliable than session.get with non-PK)
        if valid_email and User.query.filter_by(email=valid_email).first():
             flash('Email address already registered. Please log in.', 'warning'); error = True
        if error:
            return render_template('signup.html', form_data=request.form)
        new_user = User(email=valid_email, name=name, phone=phone)
        new_user.set_password(password)
        db.session.add(new_user)
        try:
            db.session.commit()
            flash('Account created successfully! You are now logged in.', 'success')
            login_user(new_user)
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Database error creating account. Please try again later.', 'error')
            print(f"Error during signup commit: {e}")
            return render_template('signup.html', form_data=request.form)
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        try:
            valid_email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
             flash('Invalid email format.', 'error')
             return render_template('login.html', form_data=request.form)
        user = User.query.filter_by(email=valid_email).first()
        if not user or not user.check_password(password):
            flash('Invalid email or password. Please try again.', 'error')
            return render_template('login.html', form_data=request.form)
        login_user(user, remember=remember)
        flash('Logged in successfully.', 'success')
        next_page = request.args.get('next')
        # Basic security check for open redirect
        if next_page and not next_page.startswith('/'): next_page = url_for('index')
        return redirect(next_page or url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('login'))

# --- Main Execution and DB Initialization ---
if __name__ == '__main__':
    with app.app_context():
        print("Initializing database schema...")
        db.create_all()
        print("Database schema initialization complete.")
    app.run(debug=True, port=5001)